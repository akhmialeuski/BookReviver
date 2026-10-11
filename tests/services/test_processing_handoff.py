"""Tests for the end of a run, which stores its final state and the collection it queues in one block."""

from contextlib import asynccontextmanager
from datetime import timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import anyio
import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.jobs.recording import RecordingJobQueue
from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.domain.enums import JobKind, JobState, Stage
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.events import JobChanged
from bookreviver.domain.ids import PageVersionId
from bookreviver.domain.values import StageRun, TileCut
from bookreviver.services.processing_parts import NOT_QUEUED
from tests.helpers.builders import EPOCH
from tests.helpers.fakes_imports import TickingClock

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Collection, Sequence

    from bookreviver.domain.entities import Actor, Job, Project
    from bookreviver.domain.ids import ProjectId
    from bookreviver.domain.values import MetadataMap
    from bookreviver.services.processing_parts import JobStarter
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

# Names of the test arguments that the parametrized kinds of job are passed in
REQUESTED_ARGUMENT: str = 'requested'
ACTIVE_ARGUMENT: str = 'active'
BUSY_REASON: str = 'project is busy'
# A tile cutting of a version that is not stored, which a worker passes over
NO_SUCH_TILES: MetadataMap = TileCut(version_ids=(PageVersionId(str(uuid4())),)).to_map()


async def prepared_page(kit: ProcessingKit) -> tuple[Actor, Project]:
    """Seed a project with one scan page whose base version is stored, the input of the geometry stage.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The actor and the project.
    :rtype: tuple[Actor, Project]
    """
    actor, project = await kit.seed_project()
    page, _ = await kit.seed_scan_page(project)
    await kit.seed_base_version(page)
    return actor, project


async def processing_kinds(kit: ProcessingKit, project_id: ProjectId) -> list[JobKind]:
    """Read the kinds of the jobs that keep the project from starting another, as the database holds them.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param project_id: Project whose jobs are read.
    :type project_id: ProjectId
    :returns: The kinds of the queued or running processing jobs, newest first.
    :rtype: list[JobKind]
    """
    active = await kit.uow().jobs.list_for_project(project_id, JobState.active())
    return [job.kind for job in active if job.kind in JobKind.processing()]


class TestRunHandOff:
    """Tests for the hand-off from a finished run to the collection of the project."""

    async def test_project_is_never_free_between_the_run_and_its_collection(
        self, fx_kit: ProcessingKit, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify every block of the worker leaves a processing job active, down to the one that ends the run.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param monkeypatch: Fixture patching the unit of work to read the database after each block.
        :type monkeypatch: pytest.MonkeyPatch
        """
        actor, project = await prepared_page(fx_kit)
        job = await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        seen: list[list[JobKind]] = []
        changed = InMemoryUnitOfWork.change

        @asynccontextmanager
        async def change_and_look(self: InMemoryUnitOfWork) -> AsyncIterator[None]:
            """Open a ``change`` block, then record which processing jobs the database shows as active once it ended.

            :param self: The unit of work that opens the block.
            :type self: InMemoryUnitOfWork
            :returns: Iterator yielding once the block is open.
            :rtype: AsyncIterator[None]
            """
            async with changed(self):
                yield
            seen.append(await processing_kinds(fx_kit, project.id))

        monkeypatch.setattr(InMemoryUnitOfWork, 'change', change_and_look)
        await fx_kit.jobs().run_stage(job.id)

        stored = await fx_kit.uow().jobs.get(job.id)
        expect(stored.state is JobState.SUCCEEDED)
        expect(seen and all(kinds for kinds in seen))
        expect(seen[-1] == [JobKind.COLLECT_VERSIONS])
        assert_expectations()

    async def test_run_started_right_after_the_finish_waits_for_the_collection_and_starts_after_it(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a run asked for on seeing a run end is stored, is not queued to a worker, and is after the collection.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await prepared_page(fx_kit)
        job = await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        await fx_kit.jobs().run_stage(job.id)
        again = await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        collection = fx_kit.recording.enqueued[-1]
        expect([queued.id for queued in fx_kit.recording.enqueued] == [job.id, collection.id])
        expect((collection.kind, again.state) == (JobKind.COLLECT_VERSIONS, JobState.QUEUED))

        await fx_kit.jobs().collect_versions(collection.id)
        expect(fx_kit.recording.enqueued[-1].id == again.id)
        assert_expectations()

    async def test_second_run_is_refused_while_a_run_is_active(self, fx_kit: ProcessingKit) -> None:
        """Verify a run is still refused with a readable reason while another run of the project is queued.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await prepared_page(fx_kit)
        await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        with pytest.raises(ConflictError, match=BUSY_REASON):
            await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))

    @pytest.mark.parametrize(REQUESTED_ARGUMENT, [JobKind.RUN_STAGE, JobKind.MEASURE_BOOK])
    @pytest.mark.parametrize('housekeeping', [JobKind.CUT_TILES, JobKind.COLLECT_VERSIONS])
    async def test_request_waits_behind_housekeeping_and_is_queued_when_it_ends(
        self, fx_kit: ProcessingKit, requested: JobKind, housekeeping: JobKind
    ) -> None:
        """Verify a run or a measure asked for during a tile cutting or a collection is stored, and queued after it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param requested: The kind of job the user asks for.
        :type requested: JobKind
        :param housekeeping: The kind of the job that is active when it is asked for.
        :type housekeeping: JobKind
        """
        _, project = await prepared_page(fx_kit)
        starter = fx_kit.parts(fx_kit.uow()).starter
        params = NO_SUCH_TILES if housekeeping is JobKind.CUT_TILES else starter.new_collection(project.id).params
        blocker = await starter.enqueue(project.id, housekeeping, params)
        waiting = await starter.enqueue(project.id, requested, StageRun(stage=Stage.GEOMETRY).to_map())
        expect(waiting.state is JobState.QUEUED)
        expect(fx_kit.recording.enqueued == [blocker])
        expect(set(await processing_kinds(fx_kit, project.id)) == {requested, housekeeping})

        method = 'cut_tiles' if housekeeping is JobKind.CUT_TILES else 'collect_versions'
        await getattr(fx_kit.jobs(), method)(blocker.id)
        expect(fx_kit.recording.enqueued == [blocker, waiting])
        assert_expectations()

    async def test_housekeeping_waits_behind_a_run_and_a_second_one_is_refused(self, fx_kit: ProcessingKit) -> None:
        """Verify a tile cutting asked for during a run is stored and queued after it, and a second cutting is refused.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await prepared_page(fx_kit)
        run = await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        starter = fx_kit.parts(fx_kit.uow()).starter
        tiles = await starter.enqueue(project.id, JobKind.CUT_TILES, NO_SUCH_TILES)
        with pytest.raises(ConflictError, match=BUSY_REASON):
            await starter.enqueue(project.id, JobKind.CUT_TILES, NO_SUCH_TILES)
        await fx_kit.jobs().run_stage(run.id)
        expect([queued.id for queued in fx_kit.recording.enqueued] == [run.id, tiles.id])
        assert_expectations()

    async def test_refused_queue_marks_the_collection_failed(self, fx_kit: ProcessingKit) -> None:
        """Verify a queue that refuses the collection leaves it failed with the reason, and the run succeeded.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await prepared_page(fx_kit)
        job = await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        with patch.object(RecordingJobQueue, 'enqueue', new=AsyncMock(side_effect=ConnectionError)):
            await fx_kit.jobs().run_stage(job.id)

        jobs = await fx_kit.uow().jobs.list_for_project(project.id, set(JobState))
        states = {stored.kind: (stored.state, stored.error) for stored in jobs}
        expect(states[JobKind.RUN_STAGE][0] is JobState.SUCCEEDED)
        expect(states[JobKind.COLLECT_VERSIONS] == (JobState.FAILED, NOT_QUEUED))
        expect(await processing_kinds(fx_kit, project.id) == [])
        assert_expectations()

    async def test_collection_is_left_out_while_another_job_has_taken_the_project(self, fx_kit: ProcessingKit) -> None:
        """Verify a run that was cancelled and replaced by another job does not queue a collection behind that job.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await prepared_page(fx_kit)
        job = await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        parts = fx_kit.parts(fx_kit.uow())
        running = await parts.tracker.start(job.id)
        assert running is not None
        # The account holder cancels the run, and a new run of another stage takes the project before the worker ends
        canceller = fx_kit.uow()
        cancelled = evolve(running, state=JobState.CANCELLED, finished_at=fx_kit.clock.now())
        async with canceller.change():
            await canceller.jobs.update_if_state(cancelled, expected=(JobState.RUNNING,))
        fx_kit.clock.moment += timedelta(seconds=1)
        replacement = await fx_kit.service().start_run(actor, project.id, Stage.CLEANUP, StageRun(stage=Stage.CLEANUP))

        await parts.tracker.finish(running, JobState.SUCCEEDED, follow_up=parts.starter.new_collection(project.id))
        expect(await processing_kinds(fx_kit, project.id) == [JobKind.RUN_STAGE])
        # The replacement was queued to a worker once, when it was asked for, and the end of the old run adds nothing
        expect([queued.id for queued in fx_kit.recording.enqueued] == [job.id, replacement.id])
        assert_expectations()


class TestRunTakesTheProjectFromAPreview:
    """Tests for a run or a measure of the book that finds a preview of the project queued or running."""

    @pytest.mark.parametrize(REQUESTED_ARGUMENT, [JobKind.RUN_STAGE, JobKind.MEASURE_BOOK])
    async def test_queued_preview_is_cancelled_and_the_request_is_queued(
        self, fx_kit: ProcessingKit, requested: JobKind
    ) -> None:
        """Verify a run or a measure asked for while a preview is queued cancels it and is queued to a worker.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param requested: The kind of job the user asks for.
        :type requested: JobKind
        """
        _, project = await prepared_page(fx_kit)
        starter = fx_kit.parts(fx_kit.uow()).starter
        preview = await starter.enqueue(project.id, JobKind.PREVIEW_STEP, {})
        asked = await starter.enqueue(project.id, requested, StageRun(stage=Stage.GEOMETRY).to_map())

        stored = await fx_kit.uow().jobs.get(preview.id)
        expect((stored.state, stored.finished_at) == (JobState.CANCELLED, fx_kit.clock.now()))
        expect(asked.state is JobState.QUEUED)
        expect(fx_kit.recording.enqueued == [preview, asked])
        expect(await processing_kinds(fx_kit, project.id) == [requested])
        assert_expectations()

    @pytest.mark.parametrize(REQUESTED_ARGUMENT, [JobKind.RUN_STAGE, JobKind.MEASURE_BOOK])
    async def test_running_preview_is_cancelled_and_the_request_is_queued_once(
        self, fx_kit: ProcessingKit, requested: JobKind
    ) -> None:
        """Verify a preview cancelled while it runs leaves the request queued once, whatever its worker does after.

        The worker of the preview goes on to the end of its steps, since it reads no state before then. Its last write
        finds the job cancelled and changes nothing, and the hand-off it ends with queues no job stored after the
        cancellation, which is the request.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param requested: The kind of job the user asks for.
        :type requested: JobKind
        """
        fx_kit.clock = TickingClock(EPOCH)
        _, project = await prepared_page(fx_kit)
        parts = fx_kit.parts(fx_kit.uow())
        preview = await parts.starter.enqueue(project.id, JobKind.PREVIEW_STEP, {})
        running = await parts.tracker.start(preview.id)
        assert running is not None

        asked = await parts.starter.enqueue(project.id, requested, StageRun(stage=Stage.GEOMETRY).to_map())
        await parts.tracker.finish(running, JobState.SUCCEEDED, total=1)

        stored = await fx_kit.uow().jobs.get(preview.id)
        expect(stored.state is JobState.CANCELLED)
        expect(fx_kit.recording.enqueued == [preview, asked])
        expect(await processing_kinds(fx_kit, project.id) == [requested])
        assert_expectations()

    async def test_request_waits_behind_housekeeping_that_the_cancelled_preview_waited_for(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a preview that waited for a tile cutting is cancelled, never queued, and the run is queued after it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project = await prepared_page(fx_kit)
        starter = fx_kit.parts(fx_kit.uow()).starter
        tiles = await starter.enqueue(project.id, JobKind.CUT_TILES, NO_SUCH_TILES)
        preview = await starter.enqueue(project.id, JobKind.PREVIEW_STEP, {})
        run = await starter.enqueue(project.id, JobKind.RUN_STAGE, StageRun(stage=Stage.GEOMETRY).to_map())
        expect(fx_kit.recording.enqueued == [tiles])

        await fx_kit.jobs().cut_tiles(tiles.id)
        expect(fx_kit.recording.enqueued == [tiles, run])
        expect((await fx_kit.uow().jobs.get(preview.id)).state is JobState.CANCELLED)
        assert_expectations()

    async def test_cancelled_preview_is_announced(self, fx_kit: ProcessingKit) -> None:
        """Verify the browser is told the preview was cancelled, so it stops waiting for it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await prepared_page(fx_kit)
        starter = fx_kit.parts(fx_kit.uow()).starter
        preview = await starter.enqueue(project.id, JobKind.PREVIEW_STEP, {})
        run = await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))

        changes = [event.job for event in fx_kit.events.published if isinstance(event, JobChanged)]
        expect(
            [(job.id, job.state) for job in changes]
            == [
                (preview.id, JobState.QUEUED),
                (preview.id, JobState.CANCELLED),
                (run.id, JobState.QUEUED),
            ]
        )
        assert_expectations()

    @pytest.mark.parametrize(REQUESTED_ARGUMENT, [JobKind.RUN_STAGE, JobKind.MEASURE_BOOK])
    @pytest.mark.parametrize(ACTIVE_ARGUMENT, [JobKind.RUN_STAGE, JobKind.MEASURE_BOOK])
    async def test_run_or_measure_is_refused_while_another_is_active(
        self, fx_kit: ProcessingKit, requested: JobKind, active: JobKind
    ) -> None:
        """Verify a run or a measure is still refused by an active run or measure, and the preview is left alone.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param requested: The kind of job the user asks for.
        :type requested: JobKind
        :param active: The kind of the job that is queued.
        :type active: JobKind
        """
        _, project = await prepared_page(fx_kit)
        starter = fx_kit.parts(fx_kit.uow()).starter
        await starter.enqueue(project.id, active, StageRun(stage=Stage.GEOMETRY).to_map())
        with pytest.raises(ConflictError, match=BUSY_REASON):
            await starter.enqueue(project.id, requested, StageRun(stage=Stage.GEOMETRY).to_map())
        expect(await processing_kinds(fx_kit, project.id) == [active])
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
        _, project = await prepared_page(fx_kit)
        starter = fx_kit.parts(fx_kit.uow()).starter
        await starter.enqueue(project.id, active, StageRun(stage=Stage.GEOMETRY).to_map())
        with pytest.raises(ConflictError, match=BUSY_REASON):
            await starter.enqueue(project.id, JobKind.PREVIEW_STEP, {})
        expect(await processing_kinds(fx_kit, project.id) == [active])
        assert_expectations()


def params_of(starter: JobStarter, project_id: ProjectId, kind: JobKind) -> MetadataMap:
    """Give the parameters a job of a kind is asked for with.

    :param starter: Starter whose collection is described.
    :type starter: JobStarter
    :param project_id: Project the job works on.
    :type project_id: ProjectId
    :param kind: The kind of job.
    :type kind: JobKind
    :returns: The parameters, which the worker is never asked to read in these tests.
    :rtype: MetadataMap
    """
    match kind:
        case JobKind.CUT_TILES:
            return NO_SUCH_TILES
        case JobKind.COLLECT_VERSIONS:
            return starter.new_collection(project_id).params
        case JobKind.PREVIEW_STEP:
            return {}
        case _:
            return StageRun(stage=Stage.GEOMETRY).to_map()


class TestConcurrentRequests:
    """Tests for two requests of one project that meet at the check of the active jobs.

    The first request is held inside its block after it read the active jobs, the second request is let in, and the test
    proves the second one waits for the first to commit before it reads anything, so the check and the insert of the
    first cannot be split by the second.
    """

    async def ask_while_the_first_waits(
        self, fx_kit: ProcessingKit, monkeypatch: pytest.MonkeyPatch, first: JobKind, second: JobKind
    ) -> tuple[Project, list[Job | ConflictError | None]]:
        """Let two requests meet at the check, the first held after its read, and give what each of them got.

        What they got is given by the order of the requests, since both may ask for the same kind of job.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param monkeypatch: Fixture holding the first request after it read the active jobs.
        :type monkeypatch: pytest.MonkeyPatch
        :param first: The kind of job the first request asks for.
        :type first: JobKind
        :param second: The kind of job the second request asks for.
        :type second: JobKind
        :returns: The project, and the job or the refusal of the first request and of the second, in that order.
        :rtype: tuple[Project, list[Job | ConflictError | None]]
        """
        _, project = await prepared_page(fx_kit)
        first_uow = fx_kit.uow()
        first_starter = fx_kit.parts(first_uow).starter
        second_starter = fx_kit.parts(fx_kit.uow()).starter
        listed = anyio.Event()
        resume = anyio.Event()
        listing = first_uow.jobs.list_for_project

        async def list_then_wait(project_id: ProjectId, states: Collection[JobState]) -> Sequence[Job]:
            """List the active jobs as they are, then hold the first request until the test lets it go.

            :param project_id: Project whose jobs are listed.
            :type project_id: ProjectId
            :param states: States a listed job may be in.
            :type states: Collection[JobState]
            :returns: The jobs found.
            :rtype: Sequence[Job]
            """
            found = await listing(project_id, states)
            listed.set()
            await resume.wait()
            return found

        monkeypatch.setattr(first_uow.jobs, 'list_for_project', list_then_wait)
        outcomes: list[Job | ConflictError | None] = [None, None]

        async def ask(starter: JobStarter, kind: JobKind, order: int) -> None:
            """Ask for a job and keep the job, or the refusal, in the place of the request.

            :param starter: Starter of the request.
            :type starter: JobStarter
            :param kind: The kind of job asked for.
            :type kind: JobKind
            :param order: Place of the request, 0 for the first and 1 for the second.
            :type order: int
            """
            try:
                outcomes[order] = await starter.enqueue(project.id, kind, params_of(starter, project.id, kind))
            except ConflictError as error:
                outcomes[order] = error

        async with anyio.create_task_group() as group:
            group.start_soon(ask, first_starter, first, 0)
            await listed.wait()
            group.start_soon(ask, second_starter, second, 1)
            await anyio.wait_all_tasks_blocked()
            expect(await fx_kit.uow().jobs.list_for_project(project.id, set(JobState)) == [])
            expect(outcomes[1] is None)
            resume.set()
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
        self, fx_kit: ProcessingKit, monkeypatch: pytest.MonkeyPatch, first: JobKind, second: JobKind
    ) -> None:
        """Verify the second request waits for the first, reads its job, and is refused as busy, storing nothing.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param monkeypatch: Fixture holding the first request after it read the active jobs.
        :type monkeypatch: pytest.MonkeyPatch
        :param first: The kind of job the first request asks for.
        :type first: JobKind
        :param second: The kind of job the second request asks for, whose group the first took.
        :type second: JobKind
        """
        project, (won, lost) = await self.ask_while_the_first_waits(fx_kit, monkeypatch, first, second)
        expect(won is not None and not isinstance(won, ConflictError))
        expect(isinstance(lost, ConflictError))
        expect(await processing_kinds(fx_kit, project.id) == [first])
        assert_expectations()

    @pytest.mark.parametrize(REQUESTED_ARGUMENT, [JobKind.RUN_STAGE, JobKind.MEASURE_BOOK])
    async def test_run_that_waited_for_a_preview_of_a_rival_request_cancels_it(
        self, fx_kit: ProcessingKit, monkeypatch: pytest.MonkeyPatch, requested: JobKind
    ) -> None:
        """Verify a run or a measure that finds the preview of a rival request in its way cancels it and is queued.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param monkeypatch: Fixture holding the preview request after it read the active jobs.
        :type monkeypatch: pytest.MonkeyPatch
        :param requested: The kind of job the second request asks for.
        :type requested: JobKind
        """
        project, (preview, asked) = await self.ask_while_the_first_waits(
            fx_kit, monkeypatch, JobKind.PREVIEW_STEP, requested
        )
        assert preview is not None
        assert not isinstance(preview, ConflictError)
        assert asked is not None
        assert not isinstance(asked, ConflictError)
        expect((await fx_kit.uow().jobs.get(preview.id)).state is JobState.CANCELLED)
        expect(asked.state is JobState.QUEUED)
        expect(fx_kit.recording.enqueued == [preview, asked])
        expect(await processing_kinds(fx_kit, project.id) == [requested])
        assert_expectations()
