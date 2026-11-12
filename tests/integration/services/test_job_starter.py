"""Tests for the admission of the jobs of a book to the board: what it refuses, what it queues and what it cancels."""

from operator import attrgetter
from typing import TYPE_CHECKING
from uuid import uuid4

import anyio
import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.errors import ConflictError
from bookreviver.domain.events import JobChanged, PageVersionReady
from bookreviver.domain.ids import PageVersionId
from bookreviver.domain.jobs import JobKind, JobState, StageRun, StepPreview, TileCut
from bookreviver.domain.recipes import Step
from bookreviver.domain.stages import Stage
from tests.harness.builders.domain import directory_of
from tests.harness.builders.processing import open_book
from tests.harness.concurrency.held_steps import steps_held
from tests.harness.fakes.processors import FakeProcessor

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from bookreviver.core.book import Book
    from bookreviver.domain.accounts import Actor
    from bookreviver.domain.ids import BookId, JobId
    from bookreviver.domain.jobs import Job, JobWork
    from bookreviver.domain.pages import Page
    from bookreviver.services.processing_parts import JobStarter
    from tests.harness.builders.domain import BookOpener
    from tests.harness.builders.processing import ProcessingKit

pytestmark = pytest.mark.anyio

# Names of the test arguments that the parametrized kinds of job are passed in
REQUESTED_ARGUMENT: str = 'requested'
HOUSEKEEPING_ARGUMENT: str = 'housekeeping'
EARLIER_ARGUMENT: str = 'earlier'
LATER_ARGUMENT: str = 'later'
# A tile cutting of a version that is not stored, which the job passes over
NO_SUCH_VERSION: PageVersionId = PageVersionId(str(uuid4()))
NO_SUCH_TILES: TileCut = TileCut(version_ids=(NO_SUCH_VERSION,))
# The ways the user asks for a collection and for a measure of the book, from the processing service
ASK_FOR_COLLECTION = attrgetter('start_collection')
ASK_FOR_MEASURE = attrgetter('start_measure')


async def prepared_page(kit: ProcessingKit) -> tuple[Actor, BookId, Page]:
    """Seed a book with one scan page whose base version is stored, the input of the geometry stage.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The actor, the book and the page.
    :rtype: tuple[Actor, BookRecord, Page]
    """
    actor, book_id = await kit.seed_book()
    page, _ = await kit.seed_scan_page(book_id)
    await kit.seed_base_version(book_id, page)
    return actor, book_id, page


def processing_kinds(kit: ProcessingKit, book_id: BookId) -> list[JobKind]:
    """Read the kinds of the jobs that keep the book from starting another, as the board holds them.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: Book whose jobs are read.
    :type book_id: BookId
    :returns: The kinds of the queued or running processing jobs, newest first.
    :rtype: list[JobKind]
    """
    return [job.kind for job in kit.board.jobs(book_id, active=True) if job.kind in JobKind.processing()]


def began(kit: ProcessingKit) -> list[JobId]:
    """List the jobs the board started, in the order it started them.

    A running job is announced when it starts and again with every step of its progress, so a job is listed once, at
    its first announcement as running.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The identifiers of the jobs that were announced as running, each once.
    :rtype: list[JobId]
    """
    started = (
        event.job.id
        for event in kit.events.published
        if isinstance(event, JobChanged) and event.job.state is JobState.RUNNING
    )
    return list(dict.fromkeys(started))


def params_of(starter: JobStarter, kind: JobKind) -> JobWork | None:
    """Give the work a job of a kind is asked for with, which a job that is run reads without failing.

    :param starter: Starter whose collection is described.
    :type starter: JobStarter
    :param kind: The kind of job.
    :type kind: JobKind
    :returns: The work, or None for a job that is put with none.
    :rtype: JobWork | None
    """
    match kind:
        case JobKind.CUT_TILES:
            return NO_SUCH_TILES
        case JobKind.COLLECT_VERSIONS:
            return starter.collection()
        case JobKind.PREVIEW_STEP | JobKind.MEASURE_BOOK:
            return None
        case _:
            return StageRun(stage=Stage.GEOMETRY)


def preview_of(page: Page) -> StepPreview:
    """Describe the preview of the first step of a geometry form on a page.

    :param page: The page.
    :type page: Page
    :returns: The preview a job can run.
    :rtype: StepPreview
    """
    return StepPreview(
        page_id=page.id, stage=Stage.GEOMETRY, steps=(Step(processor_key=FakeProcessor.spec.key),), step_index=0
    )


class TestRunFollowUp:
    """Tests for the collection a run queues behind itself, and the jobs that wait for the book meanwhile."""

    async def test_book_is_never_free_between_the_run_and_its_collection(
        self, fx_kit: ProcessingKit, monkeypatch: pytest.MonkeyPatch, fx_open_book: BookOpener
    ) -> None:
        """Verify the collection is put while the run is still active, so no request finds the book free between.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param monkeypatch: Fixture wrapping the queue of the book to note which jobs are active whenever a job is put.
        :type monkeypatch: pytest.MonkeyPatch
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id, _ = await prepared_page(fx_kit)
        await fx_kit.service().start_run(fx_open_book(actor, book_id), Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        seen: list[tuple[JobKind, list[JobKind]]] = []
        queue = fx_kit.board.open(directory_of(book_id))
        put = queue.put

        def put_and_look(job: Job) -> Job:
            """Note which processing jobs are active just before the job is put, then put it.

            :param job: The job to put.
            :type job: Job
            :returns: The job as the queue of the book holds it.
            :rtype: Job
            """
            seen.append((job.kind, processing_kinds(fx_kit, book_id)))
            return put(job)

        monkeypatch.setattr(queue, 'put', put_and_look)
        await fx_kit.work_queue()
        expect(seen == [(JobKind.COLLECT_VERSIONS, [JobKind.RUN_STAGE])])
        expect(processing_kinds(fx_kit, book_id) == [])
        assert_expectations()

    async def test_cancelled_run_queues_no_collection_of_its_own(
        self, fx_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify a run cancelled and replaced by another job puts no collection behind the replacement for itself.

        The replacement is a run of another stage, asked for while the cancelled run is still inside its task, and the
        only collection is the one the replacement queues when it ends.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id, _ = await prepared_page(fx_kit)
        run = await fx_kit.service().start_run(
            fx_open_book(actor, book_id), Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY)
        )
        async with steps_held(fx_kit):
            await fx_kit.board.cancel(run.id)
            replacement = await fx_kit.service().start_run(
                fx_open_book(actor, book_id), Stage.CLEANUP, StageRun(stage=Stage.CLEANUP)
            )
        expect([job.kind for job in fx_kit.board.put_jobs] == [JobKind.RUN_STAGE] * 2 + [JobKind.COLLECT_VERSIONS])
        expect(began(fx_kit)[:2] == [run.id, replacement.id])
        expect(fx_kit.board.get(run.id).state is JobState.CANCELLED)
        assert_expectations()


class TestCancelledRunHoldsTheBook:
    """Tests for a job asked for right after a run was cancelled, which waits for the task the run is inside."""

    @pytest.mark.parametrize('ask', [ASK_FOR_COLLECTION, ASK_FOR_MEASURE], ids=['collection', 'measure'])
    async def test_job_asked_for_after_the_cancel_starts_only_when_the_task_under_way_has_returned(
        self, fx_kit: ProcessingKit, ask: attrgetter[Callable[[Book], Awaitable[Job]]], fx_open_book: BookOpener
    ) -> None:
        """Verify the book is not worked on by two jobs at once, though the cancelled run no longer counts as active.

        The step of the run is held inside the processor, the run is cancelled, and a collection or a measure of the
        book is asked for. The request is accepted and stays queued while the step is held, and it begins after the
        step returned and stored its version.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param ask: The way to ask for the job from the processing service.
        :type ask: attrgetter[Callable[[Book], Awaitable[Job]]]
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id, _ = await prepared_page(fx_kit)
        run = await fx_kit.service().start_run(
            fx_open_book(actor, book_id), Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY)
        )
        async with steps_held(fx_kit):
            await fx_kit.board.cancel(run.id)
            asked = await ask(fx_kit.service())(fx_open_book(actor, book_id))
            while_held = (fx_kit.board.get(run.id).state, fx_kit.board.get(asked.id).state, began(fx_kit))
        published = fx_kit.events.published
        step_returned = next(index for index, event in enumerate(published) if isinstance(event, PageVersionReady))
        asked_began = next(
            index
            for index, event in enumerate(published)
            if isinstance(event, JobChanged) and (event.job.id, event.job.state) == (asked.id, JobState.RUNNING)
        )
        expect(while_held == (JobState.CANCELLED, JobState.QUEUED, [run.id]))
        expect(step_returned < asked_began)
        expect(began(fx_kit) == [run.id, asked.id])
        assert_expectations()


class TestRunHandOff:
    """Tests for the jobs a book queues behind a run, and the run it is asked for while it waits."""

    @pytest.mark.parametrize(REQUESTED_ARGUMENT, [JobKind.RUN_STAGE, JobKind.MEASURE_BOOK])
    @pytest.mark.parametrize(HOUSEKEEPING_ARGUMENT, [JobKind.CUT_TILES, JobKind.COLLECT_VERSIONS])
    async def test_request_waits_behind_housekeeping_and_starts_after_it(
        self, fx_kit: ProcessingKit, requested: JobKind, housekeeping: JobKind
    ) -> None:
        """Verify a run or a measure asked for during a tile cutting or a collection is queued, and starts after it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param requested: The kind of job the user asks for.
        :type requested: JobKind
        :param housekeeping: The kind of the job that is active when it is asked for.
        :type housekeeping: JobKind
        """
        _, book_id, _ = await prepared_page(fx_kit)
        starter = fx_kit.parts().starter
        blocker = await starter.enqueue(
            await open_book(fx_kit, book_id), housekeeping, params_of(starter, housekeeping)
        )
        waiting = await starter.enqueue(await open_book(fx_kit, book_id), requested, params_of(starter, requested))
        expect(waiting.state is JobState.QUEUED)
        expect(fx_kit.board.put_jobs == [blocker, waiting])
        expect(set(processing_kinds(fx_kit, book_id)) == {requested, housekeeping})
        await fx_kit.work_queue()
        expect(began(fx_kit)[:2] == [blocker.id, waiting.id])
        assert_expectations()


class TestFirstInFirstOut:
    """Tests for the order of the jobs of a book: each starts when the one put before it has ended."""

    @pytest.mark.parametrize(
        (EARLIER_ARGUMENT, LATER_ARGUMENT),
        [
            (JobKind.RUN_STAGE, JobKind.RUN_STAGE),
            (JobKind.RUN_STAGE, JobKind.MEASURE_BOOK),
            (JobKind.MEASURE_BOOK, JobKind.RUN_STAGE),
            (JobKind.MEASURE_BOOK, JobKind.MEASURE_BOOK),
            (JobKind.CUT_TILES, JobKind.CUT_TILES),
            (JobKind.COLLECT_VERSIONS, JobKind.CUT_TILES),
            (JobKind.RUN_STAGE, JobKind.PREVIEW_STEP),
        ],
        ids=[
            'run-then-run',
            'run-then-measure',
            'measure-then-run',
            'measure-then-measure',
            'tiles-then-tiles',
            'collection-then-tiles',
            'run-then-preview',
        ],
    )
    async def test_a_job_asked_for_while_another_is_active_waits_and_starts_after_it(
        self, fx_kit: ProcessingKit, earlier: JobKind, later: JobKind
    ) -> None:
        """Verify no job is refused because the book is busy: the later one is queued and starts when the earlier ends.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param earlier: The kind of the job asked for first.
        :type earlier: JobKind
        :param later: The kind of the job asked for while the first is queued.
        :type later: JobKind
        """
        _, book_id, _ = await prepared_page(fx_kit)
        starter = fx_kit.parts().starter
        first = await starter.enqueue(await open_book(fx_kit, book_id), earlier, params_of(starter, earlier))
        second = await starter.enqueue(await open_book(fx_kit, book_id), later, params_of(starter, later))
        expect(second.state is JobState.QUEUED)
        expect(fx_kit.board.put_jobs == [first, second])
        await fx_kit.work_queue()
        expect(began(fx_kit)[:2] == [first.id, second.id])
        assert_expectations()

    async def test_a_second_run_of_a_stage_is_queued_behind_the_first(
        self, fx_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify a run asked for through the service while a run of the book is queued is a job, and no refusal.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id, _ = await prepared_page(fx_kit)
        run = StageRun(stage=Stage.GEOMETRY)
        first = await fx_kit.service().start_run(fx_open_book(actor, book_id), Stage.GEOMETRY, run)
        second = await fx_kit.service().start_run(fx_open_book(actor, book_id), Stage.GEOMETRY, run)
        expect(fx_kit.board.put_jobs == [first, second])
        expect(processing_kinds(fx_kit, book_id) == [JobKind.RUN_STAGE, JobKind.RUN_STAGE])
        assert_expectations()

    async def test_a_new_preview_takes_the_place_of_the_one_before_it(self, fx_kit: ProcessingKit) -> None:
        """Verify a preview asked for while another is queued cancels that one, so the book holds one look at a time.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_id, page = await prepared_page(fx_kit)
        starter = fx_kit.parts().starter
        before = await starter.enqueue(await open_book(fx_kit, book_id), JobKind.PREVIEW_STEP, preview_of(page))
        after = await starter.enqueue(await open_book(fx_kit, book_id), JobKind.PREVIEW_STEP, preview_of(page))
        expect(fx_kit.board.get(before.id).state is JobState.CANCELLED)
        expect([job.id for job in fx_kit.board.jobs(book_id, active=True)] == [after.id])
        assert_expectations()

    async def test_a_tile_cutting_leaves_the_preview_of_the_book_alone(self, fx_kit: ProcessingKit) -> None:
        """Verify the cutting of tiles, which the viewer asks for by itself, waits its turn and cancels no preview.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_id, page = await prepared_page(fx_kit)
        starter = fx_kit.parts().starter
        preview = await starter.enqueue(await open_book(fx_kit, book_id), JobKind.PREVIEW_STEP, preview_of(page))
        tiles = await starter.enqueue(await open_book(fx_kit, book_id), JobKind.CUT_TILES, NO_SUCH_TILES)
        expect(fx_kit.board.get(preview.id).state is JobState.QUEUED)
        expect(fx_kit.board.put_jobs == [preview, tiles])
        assert_expectations()

    async def test_a_collection_is_queued_once_and_again_behind_one_that_runs(self, fx_kit: ProcessingKit) -> None:
        """Verify a queued collection is the answer to a second one, and nothing else of the book holds it back.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_id, _ = await prepared_page(fx_kit)
        starter = fx_kit.parts().starter
        run = await starter.enqueue(await open_book(fx_kit, book_id), JobKind.RUN_STAGE, StageRun(stage=Stage.GEOMETRY))
        first = starter.queue_collection(await open_book(fx_kit, book_id))
        again = starter.queue_collection(await open_book(fx_kit, book_id))
        expect(again == first)
        expect(fx_kit.board.put_jobs == [run, first])
        assert_expectations()

    async def test_tile_cuttings_queue_behind_each_other(self, fx_kit: ProcessingKit) -> None:
        """Verify the cutting of the tiles of a version is queued while another cutting of the book is.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_id, _ = await prepared_page(fx_kit)
        starter = fx_kit.parts().starter
        first = await starter.enqueue_tiles(await open_book(fx_kit, book_id), [NO_SUCH_VERSION])
        second = await starter.enqueue_tiles(await open_book(fx_kit, book_id), [NO_SUCH_VERSION])
        expect(first is not None and second is not None and first.id != second.id)
        expect(fx_kit.board.put_jobs == [first, second])
        assert_expectations()


class TestRunTakesTheBookFromAPreview:
    """Tests for a run or a measure of the book that finds a preview of it queued or running."""

    @pytest.mark.parametrize(REQUESTED_ARGUMENT, [JobKind.RUN_STAGE, JobKind.MEASURE_BOOK])
    async def test_running_preview_is_cancelled_and_the_request_waits_for_its_task(
        self, fx_kit: ProcessingKit, requested: JobKind, fx_open_book: BookOpener
    ) -> None:
        """Verify a preview cancelled while its step is under way leaves the request queued until the step returned.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param requested: The kind of job the user asks for.
        :type requested: JobKind
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id, page = await prepared_page(fx_kit)
        starter = fx_kit.parts().starter
        preview = await fx_kit.service().start_preview(fx_open_book(actor, book_id), preview_of(page))
        async with steps_held(fx_kit):
            asked = await starter.enqueue(await open_book(fx_kit, book_id), requested, params_of(starter, requested))
            while_held = (fx_kit.board.get(preview.id).state, fx_kit.board.get(asked.id).state, began(fx_kit))
        expect(while_held == (JobState.CANCELLED, JobState.QUEUED, [preview.id]))
        expect(began(fx_kit)[:2] == [preview.id, asked.id])
        expect(fx_kit.board.put_jobs[:2] == [preview, asked])
        assert_expectations()

    async def test_request_waits_behind_housekeeping_that_the_cancelled_preview_waited_for(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a preview queued behind a tile cutting is cancelled and never runs, and the run starts after the cutting.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_id, _ = await prepared_page(fx_kit)
        starter = fx_kit.parts().starter
        tiles = await starter.enqueue(await open_book(fx_kit, book_id), JobKind.CUT_TILES, NO_SUCH_TILES)
        preview = await starter.enqueue(await open_book(fx_kit, book_id), JobKind.PREVIEW_STEP)
        run = await starter.enqueue(
            await open_book(fx_kit, book_id), JobKind.RUN_STAGE, params_of(starter, JobKind.RUN_STAGE)
        )
        await fx_kit.work_queue()
        expect(began(fx_kit)[:2] == [tiles.id, run.id])
        expect(preview.id not in began(fx_kit))
        expect(fx_kit.board.get(preview.id).state is JobState.CANCELLED)
        assert_expectations()

    async def test_cancelled_preview_is_announced(self, fx_kit: ProcessingKit, fx_open_book: BookOpener) -> None:
        """Verify the browser is told the preview was cancelled, so it stops waiting for it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id, _ = await prepared_page(fx_kit)
        starter = fx_kit.parts().starter
        preview = await starter.enqueue(await open_book(fx_kit, book_id), JobKind.PREVIEW_STEP)
        await fx_kit.service().start_run(fx_open_book(actor, book_id), Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        changes = [event.job for event in fx_kit.events.published if isinstance(event, JobChanged)]
        assert [(job.id, job.state) for job in changes] == [(preview.id, JobState.CANCELLED)]


class TestHousekeepingRequests:
    """Tests for the collection and the tile cutting the application queues for itself."""

    async def test_collection_of_a_free_book_is_put(self, fx_kit: ProcessingKit) -> None:
        """Verify a collection asked for when no job is active is put on the board.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_id, _ = await prepared_page(fx_kit)
        collection = fx_kit.parts().starter.queue_collection(await open_book(fx_kit, book_id))
        expect(collection is not None and collection.kind is JobKind.COLLECT_VERSIONS)
        expect(fx_kit.board.put_jobs == [collection])
        assert_expectations()

    async def test_collection_asked_for_during_a_collection_gets_the_active_one(self, fx_kit: ProcessingKit) -> None:
        """Verify a book has one collection at a time, and a second request is given the one that is active.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_id, _ = await prepared_page(fx_kit)
        starter = fx_kit.parts().starter
        first = starter.queue_collection(await open_book(fx_kit, book_id))
        second = starter.queue_collection(await open_book(fx_kit, book_id))
        expect(first is not None and second == first)
        expect(fx_kit.board.put_jobs == [first])
        assert_expectations()

    async def test_tile_cutting_of_no_version_is_not_queued(self, fx_kit: ProcessingKit) -> None:
        """Verify there is nothing to put when no version needs its pyramid.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_id, _ = await prepared_page(fx_kit)
        asked = await fx_kit.parts().starter.enqueue_tiles(await open_book(fx_kit, book_id), [])
        expect(asked is None)
        expect(fx_kit.board.put_jobs == [])
        assert_expectations()

    async def test_tile_cutting_is_queued_behind_a_run(self, fx_kit: ProcessingKit, fx_open_book: BookOpener) -> None:
        """Verify a tile cutting asked for during a run is queued behind it, with the versions it must cut.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id, _ = await prepared_page(fx_kit)
        run = await fx_kit.service().start_run(
            fx_open_book(actor, book_id), Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY)
        )
        tiles = await fx_kit.parts().starter.enqueue_tiles(await open_book(fx_kit, book_id), [NO_SUCH_VERSION])
        expect(tiles is not None and tiles.state is JobState.QUEUED)
        expect(tiles is not None and tiles.asked(TileCut) == TileCut(version_ids=(NO_SUCH_VERSION,)))
        expect(fx_kit.board.put_jobs == [run, tiles])
        assert_expectations()


class TestConcurrentRequests:
    """Tests for two requests of one book asked for at once, which meet at the check of the active jobs.

    The check and the put of a request have no ``await`` between them, so the second request cannot split them and reads
    the job the first put. Both requests start in one task group, the first before the second.
    """

    async def ask_at_once(
        self, fx_kit: ProcessingKit, first: JobKind, second: JobKind
    ) -> tuple[BookId, list[Job | ConflictError | None]]:
        """Ask for two jobs of one book at once, the first before the second, and give what each got.

        What they got is given by the order of the requests, since both may ask for the same kind of job.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param first: The kind of job the first request asks for.
        :type first: JobKind
        :param second: The kind of job the second request asks for.
        :type second: JobKind
        :returns: The book, and the job or the refusal of the first request and of the second, in that order.
        :rtype: tuple[BookRecord, list[Job | ConflictError | None]]
        """
        _, book_id, _ = await prepared_page(fx_kit)
        book = await open_book(fx_kit, book_id)
        outcomes: list[Job | ConflictError | None] = [None, None]

        async def ask(kind: JobKind, order: int) -> None:
            """Ask for a job through a starter of its own and keep the job, or the refusal, in the place of the request.

            :param kind: The kind of job asked for.
            :type kind: JobKind
            :param order: Place of the request, 0 for the first and 1 for the second.
            :type order: int
            """
            starter = fx_kit.parts().starter
            try:
                outcomes[order] = await starter.enqueue(book, kind, params_of(starter, kind))
            except ConflictError as error:
                outcomes[order] = error

        async with anyio.create_task_group() as group:
            group.start_soon(ask, first, 0)
            group.start_soon(ask, second, 1)
        return book_id, outcomes

    @pytest.mark.parametrize(REQUESTED_ARGUMENT, [JobKind.RUN_STAGE, JobKind.MEASURE_BOOK])
    async def test_run_asked_for_with_the_preview_of_a_rival_request_cancels_it(
        self, fx_kit: ProcessingKit, requested: JobKind
    ) -> None:
        """Verify a run or a measure that finds the preview of a rival request in its way cancels it and is queued.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param requested: The kind of job the second request asks for.
        :type requested: JobKind
        """
        book_id, (preview, asked) = await self.ask_at_once(fx_kit, JobKind.PREVIEW_STEP, requested)
        assert preview is not None
        assert not isinstance(preview, ConflictError)
        assert asked is not None
        assert not isinstance(asked, ConflictError)
        expect(fx_kit.board.get(preview.id).state is JobState.CANCELLED)
        expect(asked.state is JobState.QUEUED)
        expect(fx_kit.board.put_jobs == [preview, asked])
        expect(processing_kinds(fx_kit, book_id) == [requested])
        assert_expectations()
