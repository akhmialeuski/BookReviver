"""Tests for the admission of the jobs of a project to the board: what it refuses, what it queues and what it cancels."""

from operator import attrgetter
from typing import TYPE_CHECKING
from uuid import uuid4

import anyio
import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import JobKind, JobState, Stage
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.events import JobChanged, PageVersionReady
from bookreviver.domain.ids import PageVersionId
from bookreviver.domain.values import StageRun, Step, StepPreview, TileCut
from tests.helpers.held_steps import steps_held
from tests.helpers.processors import FakeProcessor

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from bookreviver.domain.entities import Actor, Job, Page, Project
    from bookreviver.domain.ids import JobId, ProjectId
    from bookreviver.domain.values import MetadataMap
    from bookreviver.services.processing_parts import JobStarter
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

# Names of the test arguments that the parametrized kinds of job are passed in
REQUESTED_ARGUMENT: str = 'requested'
ACTIVE_ARGUMENT: str = 'active'
HOUSEKEEPING_ARGUMENT: str = 'housekeeping'
BUSY_REASON: str = 'project is busy'
# A tile cutting of a version that is not stored, which the job passes over
NO_SUCH_VERSION: PageVersionId = PageVersionId(str(uuid4()))
NO_SUCH_TILES: MetadataMap = TileCut(version_ids=(NO_SUCH_VERSION,)).to_map()
# The ways the user asks for a collection and for a measure of the book, from the processing service
ASK_FOR_COLLECTION = attrgetter('start_collection')
ASK_FOR_MEASURE = attrgetter('start_measure')


async def prepared_page(kit: ProcessingKit) -> tuple[Actor, Project, Page]:
    """Seed a project with one scan page whose base version is stored, the input of the geometry stage.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The actor, the project and the page.
    :rtype: tuple[Actor, Project, Page]
    """
    actor, project = await kit.seed_project()
    page, _ = await kit.seed_scan_page(project)
    await kit.seed_base_version(page)
    return actor, project, page


def processing_kinds(kit: ProcessingKit, project_id: ProjectId) -> list[JobKind]:
    """Read the kinds of the jobs that keep the project from starting another, as the board holds them.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param project_id: Project whose jobs are read.
    :type project_id: ProjectId
    :returns: The kinds of the queued or running processing jobs, newest first.
    :rtype: list[JobKind]
    """
    return [job.kind for job in kit.board.jobs(project_id, active=True) if job.kind in JobKind.processing()]


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


def params_of(starter: JobStarter, kind: JobKind) -> MetadataMap:
    """Give the parameters a job of a kind is asked for with, which a job that is run reads without failing.

    :param starter: Starter whose collection is described.
    :type starter: JobStarter
    :param kind: The kind of job.
    :type kind: JobKind
    :returns: The parameters.
    :rtype: MetadataMap
    """
    match kind:
        case JobKind.CUT_TILES:
            return NO_SUCH_TILES
        case JobKind.COLLECT_VERSIONS:
            return starter.collection().to_map()
        case JobKind.PREVIEW_STEP:
            return {}
        case _:
            return StageRun(stage=Stage.GEOMETRY).to_map()


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
    """Tests for the collection a run queues behind itself, and the jobs that wait for the project meanwhile."""

    async def test_project_is_never_free_between_the_run_and_its_collection(
        self, fx_kit: ProcessingKit, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify the collection is put while the run is still active, so no request finds the project free between.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param monkeypatch: Fixture wrapping the board to note which jobs are active whenever a job is put.
        :type monkeypatch: pytest.MonkeyPatch
        """
        actor, project, _ = await prepared_page(fx_kit)
        await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        seen: list[tuple[JobKind, list[JobKind]]] = []
        put = fx_kit.board.put

        def put_and_look(job: Job) -> Job:
            """Note which processing jobs are active just before the job is put, then put it.

            :param job: The job to put.
            :type job: Job
            :returns: The job as the board holds it.
            :rtype: Job
            """
            seen.append((job.kind, processing_kinds(fx_kit, project.id)))
            return put(job)

        monkeypatch.setattr(fx_kit.board, 'put', put_and_look)
        await fx_kit.work_queue()
        expect(seen == [(JobKind.COLLECT_VERSIONS, [JobKind.RUN_STAGE])])
        expect(processing_kinds(fx_kit, project.id) == [])
        assert_expectations()

    @pytest.mark.parametrize(HOUSEKEEPING_ARGUMENT, [JobKind.CUT_TILES, JobKind.COLLECT_VERSIONS])
    async def test_run_queues_no_collection_while_a_housekeeping_job_is_active(
        self, fx_kit: ProcessingKit, housekeeping: JobKind
    ) -> None:
        """Verify a run that ends with a tile cutting or a collection queued behind it puts no second collection.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param housekeeping: The kind of the job queued behind the run.
        :type housekeeping: JobKind
        """
        actor, project, _ = await prepared_page(fx_kit)
        run = await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        starter = fx_kit.parts(fx_kit.uow()).starter
        waiting = await starter.enqueue(project.id, housekeeping, params_of(starter, housekeeping))
        await fx_kit.work_queue()
        expect([job.kind for job in fx_kit.board.put_jobs] == [JobKind.RUN_STAGE, housekeeping])
        expect(began(fx_kit) == [run.id, waiting.id])
        assert_expectations()

    async def test_cancelled_run_queues_no_collection_of_its_own(self, fx_kit: ProcessingKit) -> None:
        """Verify a run cancelled and replaced by another job puts no collection behind the replacement for itself.

        The replacement is a run of another stage, asked for while the cancelled run is still inside its task, and the
        only collection is the one the replacement queues when it ends.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _ = await prepared_page(fx_kit)
        run = await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        async with steps_held(fx_kit):
            await fx_kit.board.cancel(run.id)
            replacement = await fx_kit.service().start_run(
                actor, project.id, Stage.CLEANUP, StageRun(stage=Stage.CLEANUP)
            )
        expect([job.kind for job in fx_kit.board.put_jobs] == [JobKind.RUN_STAGE] * 2 + [JobKind.COLLECT_VERSIONS])
        expect(began(fx_kit)[:2] == [run.id, replacement.id])
        expect(fx_kit.board.get(run.id).state is JobState.CANCELLED)
        assert_expectations()


class TestCancelledRunHoldsTheBook:
    """Tests for a job asked for right after a run was cancelled, which waits for the task the run is inside."""

    @pytest.mark.parametrize('ask', [ASK_FOR_COLLECTION, ASK_FOR_MEASURE], ids=['collection', 'measure'])
    async def test_job_asked_for_after_the_cancel_starts_only_when_the_task_under_way_has_returned(
        self, fx_kit: ProcessingKit, ask: attrgetter[Callable[[Actor, ProjectId], Awaitable[Job]]]
    ) -> None:
        """Verify the book is not worked on by two jobs at once, though the cancelled run no longer counts as active.

        The step of the run is held inside the processor, the run is cancelled, and a collection or a measure of the
        book is asked for. The request is accepted and stays queued while the step is held, and it begins after the
        step returned and stored its version.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param ask: The way to ask for the job from the processing service.
        :type ask: attrgetter[Callable[[Actor, ProjectId], Awaitable[Job]]]
        """
        actor, project, _ = await prepared_page(fx_kit)
        run = await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        async with steps_held(fx_kit):
            await fx_kit.board.cancel(run.id)
            asked = await ask(fx_kit.service())(actor, project.id)
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
    """Tests for the jobs a project queues behind a run, and the run it is asked for while it waits."""

    async def test_second_run_is_refused_while_a_run_is_active(self, fx_kit: ProcessingKit) -> None:
        """Verify a run is refused with a readable reason while another run of the project is queued.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _ = await prepared_page(fx_kit)
        await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        with pytest.raises(ConflictError, match=BUSY_REASON):
            await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))

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
        _, project, _ = await prepared_page(fx_kit)
        starter = fx_kit.parts(fx_kit.uow()).starter
        blocker = await starter.enqueue(project.id, housekeeping, params_of(starter, housekeeping))
        waiting = await starter.enqueue(project.id, requested, params_of(starter, requested))
        expect(waiting.state is JobState.QUEUED)
        expect(fx_kit.board.put_jobs == [blocker, waiting])
        expect(set(processing_kinds(fx_kit, project.id)) == {requested, housekeeping})
        await fx_kit.work_queue()
        expect(began(fx_kit)[:2] == [blocker.id, waiting.id])
        assert_expectations()

    async def test_housekeeping_waits_behind_a_run_and_a_second_one_is_refused(self, fx_kit: ProcessingKit) -> None:
        """Verify a tile cutting asked for during a run is queued behind it, and a second cutting is refused.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _ = await prepared_page(fx_kit)
        run = await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        starter = fx_kit.parts(fx_kit.uow()).starter
        tiles = await starter.enqueue(project.id, JobKind.CUT_TILES, NO_SUCH_TILES)
        with pytest.raises(ConflictError, match=BUSY_REASON):
            await starter.enqueue(project.id, JobKind.CUT_TILES, NO_SUCH_TILES)
        expect(fx_kit.board.put_jobs == [run, tiles])
        expect(tiles.state is JobState.QUEUED)
        assert_expectations()


class TestRunTakesTheProjectFromAPreview:
    """Tests for a run or a measure of the book that finds a preview of the project queued or running."""

    @pytest.mark.parametrize(REQUESTED_ARGUMENT, [JobKind.RUN_STAGE, JobKind.MEASURE_BOOK])
    async def test_queued_preview_is_cancelled_and_the_request_is_queued(
        self, fx_kit: ProcessingKit, requested: JobKind
    ) -> None:
        """Verify a run or a measure asked for while a preview is queued cancels it and is queued behind it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param requested: The kind of job the user asks for.
        :type requested: JobKind
        """
        _, project, _ = await prepared_page(fx_kit)
        starter = fx_kit.parts(fx_kit.uow()).starter
        preview = await starter.enqueue(project.id, JobKind.PREVIEW_STEP, {})
        asked = await starter.enqueue(project.id, requested, params_of(starter, requested))
        stored = fx_kit.board.get(preview.id)
        expect((stored.state, stored.finished_at) == (JobState.CANCELLED, fx_kit.clock.now()))
        expect(asked.state is JobState.QUEUED)
        expect(fx_kit.board.put_jobs == [preview, asked])
        expect(processing_kinds(fx_kit, project.id) == [requested])
        assert_expectations()

    @pytest.mark.parametrize(REQUESTED_ARGUMENT, [JobKind.RUN_STAGE, JobKind.MEASURE_BOOK])
    async def test_running_preview_is_cancelled_and_the_request_waits_for_its_task(
        self, fx_kit: ProcessingKit, requested: JobKind
    ) -> None:
        """Verify a preview cancelled while its step is under way leaves the request queued until the step returned.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param requested: The kind of job the user asks for.
        :type requested: JobKind
        """
        actor, project, page = await prepared_page(fx_kit)
        starter = fx_kit.parts(fx_kit.uow()).starter
        preview = await fx_kit.service().start_preview(actor, project.id, preview_of(page))
        async with steps_held(fx_kit):
            asked = await starter.enqueue(project.id, requested, params_of(starter, requested))
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
        _, project, _ = await prepared_page(fx_kit)
        starter = fx_kit.parts(fx_kit.uow()).starter
        tiles = await starter.enqueue(project.id, JobKind.CUT_TILES, NO_SUCH_TILES)
        preview = await starter.enqueue(project.id, JobKind.PREVIEW_STEP, {})
        run = await starter.enqueue(project.id, JobKind.RUN_STAGE, params_of(starter, JobKind.RUN_STAGE))
        await fx_kit.work_queue()
        expect(began(fx_kit)[:2] == [tiles.id, run.id])
        expect(preview.id not in began(fx_kit))
        expect(fx_kit.board.get(preview.id).state is JobState.CANCELLED)
        assert_expectations()

    async def test_cancelled_preview_is_announced(self, fx_kit: ProcessingKit) -> None:
        """Verify the browser is told the preview was cancelled, so it stops waiting for it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _ = await prepared_page(fx_kit)
        starter = fx_kit.parts(fx_kit.uow()).starter
        preview = await starter.enqueue(project.id, JobKind.PREVIEW_STEP, {})
        await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        changes = [event.job for event in fx_kit.events.published if isinstance(event, JobChanged)]
        assert [(job.id, job.state) for job in changes] == [(preview.id, JobState.CANCELLED)]

    @pytest.mark.parametrize(REQUESTED_ARGUMENT, [JobKind.RUN_STAGE, JobKind.MEASURE_BOOK])
    @pytest.mark.parametrize(ACTIVE_ARGUMENT, [JobKind.RUN_STAGE, JobKind.MEASURE_BOOK])
    async def test_run_or_measure_is_refused_while_another_is_active(
        self, fx_kit: ProcessingKit, requested: JobKind, active: JobKind
    ) -> None:
        """Verify a run or a measure is refused by an active run or measure, and the preview is left alone.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param requested: The kind of job the user asks for.
        :type requested: JobKind
        :param active: The kind of the job that is queued.
        :type active: JobKind
        """
        _, project, _ = await prepared_page(fx_kit)
        starter = fx_kit.parts(fx_kit.uow()).starter
        await starter.enqueue(project.id, active, params_of(starter, active))
        with pytest.raises(ConflictError, match=BUSY_REASON):
            await starter.enqueue(project.id, requested, params_of(starter, requested))
        expect(processing_kinds(fx_kit, project.id) == [active])
        assert_expectations()

    @pytest.mark.parametrize(ACTIVE_ARGUMENT, [JobKind.RUN_STAGE, JobKind.MEASURE_BOOK])
    async def test_preview_is_refused_while_a_run_or_a_measure_is_active(
        self, fx_kit: ProcessingKit, active: JobKind
    ) -> None:
        """Verify a preview does not take the project from a run or a measure that is queued.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param active: The kind of the job that is queued.
        :type active: JobKind
        """
        _, project, _ = await prepared_page(fx_kit)
        starter = fx_kit.parts(fx_kit.uow()).starter
        await starter.enqueue(project.id, active, params_of(starter, active))
        with pytest.raises(ConflictError, match=BUSY_REASON):
            await starter.enqueue(project.id, JobKind.PREVIEW_STEP, {})
        expect(processing_kinds(fx_kit, project.id) == [active])
        assert_expectations()


class TestHousekeepingRequests:
    """Tests for the collection and the tile cutting the application queues for itself."""

    async def test_collection_of_a_free_project_is_put(self, fx_kit: ProcessingKit) -> None:
        """Verify a collection asked for when no job is active is put on the board.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project, _ = await prepared_page(fx_kit)
        collection = await fx_kit.parts(fx_kit.uow()).starter.enqueue_collection(project.id)
        expect(collection is not None and collection.kind is JobKind.COLLECT_VERSIONS)
        expect(fx_kit.board.put_jobs == [collection])
        assert_expectations()

    @pytest.mark.parametrize(ACTIVE_ARGUMENT, [JobKind.RUN_STAGE, JobKind.MEASURE_BOOK, JobKind.CUT_TILES])
    async def test_collection_is_not_queued_beside_another_job_that_processes_the_project(
        self, fx_kit: ProcessingKit, active: JobKind
    ) -> None:
        """Verify a collection asked for during a run, a measure or a tile cutting is not put, and gets no job back.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param active: The kind of the job that is queued.
        :type active: JobKind
        """
        _, project, _ = await prepared_page(fx_kit)
        starter = fx_kit.parts(fx_kit.uow()).starter
        blocker = await starter.enqueue(project.id, active, params_of(starter, active))
        asked = await starter.enqueue_collection(project.id)
        expect(asked is None)
        expect(fx_kit.board.put_jobs == [blocker])
        assert_expectations()

    async def test_collection_asked_for_during_a_collection_gets_the_active_one(self, fx_kit: ProcessingKit) -> None:
        """Verify a project has one collection at a time, and a second request is given the one that is active.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project, _ = await prepared_page(fx_kit)
        starter = fx_kit.parts(fx_kit.uow()).starter
        first = await starter.enqueue_collection(project.id)
        second = await starter.enqueue_collection(project.id)
        expect(first is not None and second == first)
        expect(fx_kit.board.put_jobs == [first])
        assert_expectations()

    async def test_tile_cutting_of_no_version_is_not_queued(self, fx_kit: ProcessingKit) -> None:
        """Verify there is nothing to put when no version needs its pyramid.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project, _ = await prepared_page(fx_kit)
        asked = await fx_kit.parts(fx_kit.uow()).starter.enqueue_tiles(project.id, [])
        expect(asked is None)
        expect(fx_kit.board.put_jobs == [])
        assert_expectations()

    async def test_tile_cutting_is_queued_behind_a_run(self, fx_kit: ProcessingKit) -> None:
        """Verify a tile cutting asked for during a run is queued behind it, with the versions it must cut.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _ = await prepared_page(fx_kit)
        run = await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        tiles = await fx_kit.parts(fx_kit.uow()).starter.enqueue_tiles(project.id, [NO_SUCH_VERSION])
        expect(tiles is not None and tiles.state is JobState.QUEUED)
        expect(tiles is not None and TileCut.from_map(tiles.params) == TileCut(version_ids=(NO_SUCH_VERSION,)))
        expect(fx_kit.board.put_jobs == [run, tiles])
        assert_expectations()

    @pytest.mark.parametrize(HOUSEKEEPING_ARGUMENT, [JobKind.CUT_TILES, JobKind.COLLECT_VERSIONS])
    async def test_tile_cutting_is_left_out_while_another_housekeeping_job_is_active(
        self, fx_kit: ProcessingKit, housekeeping: JobKind
    ) -> None:
        """Verify a project has one tile cutting or collection at a time, so a second request puts nothing.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param housekeeping: The kind of the job that is queued.
        :type housekeeping: JobKind
        """
        _, project, _ = await prepared_page(fx_kit)
        starter = fx_kit.parts(fx_kit.uow()).starter
        blocker = await starter.enqueue(project.id, housekeeping, params_of(starter, housekeeping))
        asked = await starter.enqueue_tiles(project.id, [NO_SUCH_VERSION])
        expect(asked is None)
        expect(fx_kit.board.put_jobs == [blocker])
        assert_expectations()


class TestConcurrentRequests:
    """Tests for two requests of one project asked for at once, which meet at the check of the active jobs.

    The check and the put of a request have no ``await`` between them, so the second request cannot split them and reads
    the job the first put. Both requests start in one task group, the first before the second.
    """

    async def ask_at_once(
        self, fx_kit: ProcessingKit, first: JobKind, second: JobKind
    ) -> tuple[Project, list[Job | ConflictError | None]]:
        """Ask for two jobs of one project at once, the first before the second, and give what each got.

        What they got is given by the order of the requests, since both may ask for the same kind of job.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param first: The kind of job the first request asks for.
        :type first: JobKind
        :param second: The kind of job the second request asks for.
        :type second: JobKind
        :returns: The project, and the job or the refusal of the first request and of the second, in that order.
        :rtype: tuple[Project, list[Job | ConflictError | None]]
        """
        _, project, _ = await prepared_page(fx_kit)
        outcomes: list[Job | ConflictError | None] = [None, None]

        async def ask(kind: JobKind, order: int) -> None:
            """Ask for a job through a starter of its own and keep the job, or the refusal, in the place of the request.

            :param kind: The kind of job asked for.
            :type kind: JobKind
            :param order: Place of the request, 0 for the first and 1 for the second.
            :type order: int
            """
            starter = fx_kit.parts(fx_kit.uow()).starter
            try:
                outcomes[order] = await starter.enqueue(project.id, kind, params_of(starter, kind))
            except ConflictError as error:
                outcomes[order] = error

        async with anyio.create_task_group() as group:
            group.start_soon(ask, first, 0)
            group.start_soon(ask, second, 1)
        return project, outcomes

    @pytest.mark.parametrize(
        ('first', 'second'),
        [
            pytest.param(JobKind.RUN_STAGE, JobKind.RUN_STAGE, id='run-then-run'),
            pytest.param(JobKind.RUN_STAGE, JobKind.MEASURE_BOOK, id='run-then-measure'),
            pytest.param(JobKind.MEASURE_BOOK, JobKind.RUN_STAGE, id='measure-then-run'),
            pytest.param(JobKind.RUN_STAGE, JobKind.PREVIEW_STEP, id='run-then-preview'),
            pytest.param(JobKind.CUT_TILES, JobKind.COLLECT_VERSIONS, id='tiles-then-collection'),
            pytest.param(JobKind.COLLECT_VERSIONS, JobKind.CUT_TILES, id='collection-then-tiles'),
        ],
    )
    async def test_second_request_for_a_group_the_first_took_is_refused(
        self, fx_kit: ProcessingKit, first: JobKind, second: JobKind
    ) -> None:
        """Verify the second request reads the job of the first and is refused as busy, putting nothing.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param first: The kind of job the first request asks for.
        :type first: JobKind
        :param second: The kind of job the second request asks for, whose group the first took.
        :type second: JobKind
        """
        project, (won, lost) = await self.ask_at_once(fx_kit, first, second)
        expect(won is not None and not isinstance(won, ConflictError))
        expect(isinstance(lost, ConflictError))
        expect([job.kind for job in fx_kit.board.put_jobs] == [first])
        expect(processing_kinds(fx_kit, project.id) == [first])
        assert_expectations()

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
        project, (preview, asked) = await self.ask_at_once(fx_kit, JobKind.PREVIEW_STEP, requested)
        assert preview is not None
        assert not isinstance(preview, ConflictError)
        assert asked is not None
        assert not isinstance(asked, ConflictError)
        expect(fx_kit.board.get(preview.id).state is JobState.CANCELLED)
        expect(asked.state is JobState.QUEUED)
        expect(fx_kit.board.put_jobs == [preview, asked])
        expect(processing_kinds(fx_kit, project.id) == [requested])
        assert_expectations()
