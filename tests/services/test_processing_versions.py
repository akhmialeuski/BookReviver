"""Tests for the current version of a stage, the list of versions, tiles on request, collection and coordinates."""

from contextlib import asynccontextmanager
from datetime import timedelta
from functools import partial
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.entities import Actor
from bookreviver.domain.enums import (
    JobKind,
    JobState,
    Rendition,
    ResultMark,
    Stage,
    StageState,
    TransformKind,
    VersionData,
    VersionScale,
    VersionState,
)
from bookreviver.domain.errors import ConflictError, InvalidParametersError, NotFoundError
from bookreviver.domain.events import JobChanged, PageVersionReady
from bookreviver.domain.geometry import Point, Quad, Transform
from bookreviver.domain.ids import PageVersionId, StepId
from bookreviver.domain.keys import BookKeys
from bookreviver.domain.values import (
    CollectionReport,
    RecipeDraft,
    SliceRequest,
    StageRun,
    Step,
    StepPreview,
    TileCut,
    VersionFilter,
)
from bookreviver.services.processing_jobs import VERSIONS_LEFT, ProcessingJobs
from bookreviver.services.version_clearing import BEING_COLLECTED
from tests.helpers.builders import EPOCH, make_page_stage
from tests.helpers.held_steps import steps_held
from tests.helpers.meanwhile import change_when_published
from tests.helpers.processing import (
    IMAGE_CONTENT,
    change_versions,
    found_stage,
    found_version,
    stage_versions,
    stored_job,
)
from tests.helpers.processors import STRENGTH_PARAMETER, FakeProcessor
from tests.helpers.spreads import run_stage, stage_of

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

    from bookreviver.domain.entities import BookRecord, Job, Page, PageVersion
    from bookreviver.domain.ids import BookId, StorageKey
    from bookreviver.ports.persistence import BookRepositories
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

EVERYTHING: SliceRequest = SliceRequest(limit=100)
BATCH_ARG: str = 'batch_size'
# The attribute of the jobs that holds how many versions a collection deletes before it commits
BATCH_SIZE_ATTRIBUTE: str = 'COLLECTION_BATCH_SIZE'
STATE_ARGUMENT: str = 'state'
BUSY_REASON: str = 'book is busy'
OTHER_STAGE_REASON: str = 'another stage'
PREVIEW_REASON: str = 'preview'
# A version of the geometry stage a test adds beside the one the run made, with a strength of its own
CHOSEN_VERSION_ID: str = '0123456789abcdef'
# A second version of the page beside the old one, and a preview of it
OTHER_VERSION_ID: PageVersionId = PageVersionId('0f0f0f0f0f0f0f0f')
PREVIEW_VERSION_ID: PageVersionId = PageVersionId('fedcbafedcbafedc')
# The method of the asset store that removes the directory of a version, which the tests make fail
DELETE_PREFIX: str = 'delete_prefix'
READ_ONLY_DISK: str = 'The disk is read only.'
REFUSED_DIRECTORY: str = 'The disk refuses this directory.'
NO_FILTER: VersionFilter = VersionFilter()
HALF: Quad = Quad(
    top_left=Point(x=1100, y=0),
    top_right=Point(x=2200, y=0),
    bottom_right=Point(x=2200, y=1561),
    bottom_left=Point(x=1100, y=1561),
)


async def ran_geometry(kit: ProcessingKit) -> tuple[Actor, BookRecord, Page, PageVersion]:
    """Seed a page and run the geometry stage on it once.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The actor, the book, the page and the version the run made.
    :rtype: tuple[Actor, BookRecord, Page, PageVersion]
    """
    actor, book_record = await kit.seed_record()
    page, _ = await kit.seed_scan_page(book_record)
    await kit.seed_base_version(book_record.id, page)
    await run_stage(kit, actor, book_record, StageRun(stage=Stage.GEOMETRY))
    record = await stage_of(kit, book_record.id, page, Stage.GEOMETRY)
    assert record.head_version_id is not None
    return actor, book_record, page, await kit.stored_version(book_record.id, record.head_version_id)


async def ran_two_steps(
    kit: ProcessingKit, *, first_on: bool = True
) -> tuple[Actor, BookRecord, Page, list[StepId], list[PageVersion]]:
    """Seed a page, save a geometry recipe of two steps of one processor, and run it.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param first_on: Whether the first step is switched on.
    :type first_on: bool
    :returns: The actor, the book, the page, the identifiers of the two steps, and the versions the run made in the
              order of their steps.
    :rtype: tuple[Actor, BookRecord, Page, list[StepId], list[PageVersion]]
    """
    actor, book_record = await kit.seed_record()
    page, _ = await kit.seed_scan_page(book_record)
    await kit.seed_base_version(book_record.id, page)
    steps = [Step(processor_key=FakeProcessor.spec.key, enabled=first_on), Step(processor_key=FakeProcessor.spec.key)]
    recipe = await kit.edit_recipe(actor, book_record, Stage.GEOMETRY, RecipeDraft(steps=steps))
    await run_stage(kit, actor, book_record, StageRun(stage=Stage.GEOMETRY))
    found = await kit.uow().read_book(book_record.id, lambda repos: repos.page_versions.list_for_page(page.id))
    in_stage = [version for version in found if version.stage is Stage.GEOMETRY]
    # The steps run within one tick of the clock, so the order is the one of the chain: each step reads the version of
    # the step before it, and the first reads the image of an earlier stage
    read = {version.input_id: version for version in in_stage}
    made: list[PageVersion] = []
    next_version = next((version for version in in_stage if version.input_id not in {v.id for v in in_stage}), None)
    while next_version is not None:
        made.append(next_version)
        next_version = read.get(next_version.id)
    return actor, book_record, page, [step.step_id for step in recipe.steps], made


async def mark_version(kit: ProcessingKit, book_id: BookId, version: PageVersion, mark: ResultMark) -> None:
    """Set the mark of a stored version.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: Book that holds the version.
    :type book_id: BookId
    :param version: The version.
    :type version: PageVersion
    :param mark: The mark to set.
    :type mark: ResultMark
    """
    await change_versions(kit, book_id, lambda versions: versions.update(evolve(version, mark=mark)))


@asynccontextmanager
async def previewed(
    kit: ProcessingKit, actor: Actor, book_record: BookRecord, page: Page, state: JobState
) -> AsyncGenerator[Job]:
    """Ask for a preview of a page, which stays queued, or runs until its step is held inside the processor.

    Leaving the block lets a held step go and the board run what it holds.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: Account owning the book.
    :type actor: Actor
    :param book_record: The record of the book.
    :type book_record: BookRecord
    :param page: The page to preview a geometry step on.
    :type page: Page
    :param state: Whether the preview is left queued, or is running while its step is held.
    :type state: JobState
    :returns: Generator yielding the preview job.
    :rtype: AsyncGenerator[Job]
    """
    steps = (Step(processor_key=FakeProcessor.spec.key),)
    preview = await kit.service().start_preview(
        actor, book_record.id, StepPreview(page_id=page.id, stage=Stage.GEOMETRY, steps=steps, step_index=0)
    )
    if state is JobState.RUNNING:
        async with steps_held(kit):
            yield preview
    else:
        yield preview


class TestChooseVersion:
    """Tests for ProcessingService.choose_version."""

    async def test_old_version_becomes_current_and_the_later_stages_become_stale(self, fx_kit: ProcessingKit) -> None:
        """Verify choosing an earlier version of the geometry makes it current and marks the cleanup stale.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, page, first = await ran_geometry(fx_kit)
        second = evolve(first, id=PageVersionId(CHOSEN_VERSION_ID), params={STRENGTH_PARAMETER: 7})

        def add_second(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Add the second version, and a record of the cleanup stage that reads the first.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            repos.page_versions.add(second)
            repos.page_stages.save(make_page_stage(page_id=page.id, stage=Stage.CLEANUP, head_version_id=first.id))

        await fx_kit.uow().change_book(book_record.id, add_second)
        chosen = await fx_kit.service().choose_version(actor, book_record.id, page.id, Stage.GEOMETRY, second.id)
        later = await stage_of(fx_kit, book_record.id, page, Stage.CLEANUP)
        expect((chosen.head_version_id, chosen.state) == (second.id, StageState.FRESH))
        expect(later.state is StageState.STALE)
        assert_expectations()

    async def test_choice_is_refused_while_the_book_is_processing_something(self, fx_kit: ProcessingKit) -> None:
        """Reject the choice of a current version while a job may be reading or deleting versions, and change nothing.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, page, first = await ran_geometry(fx_kit)
        await fx_kit.service().start_collection(actor, book_record.id)
        with pytest.raises(ConflictError, match=BUSY_REASON):
            await fx_kit.service().choose_version(actor, book_record.id, page.id, Stage.GEOMETRY, first.id)

    @pytest.mark.parametrize(STATE_ARGUMENT, [JobState.QUEUED, JobState.RUNNING])
    async def test_a_preview_of_the_book_is_cancelled_and_the_choice_goes_on(
        self, fx_kit: ProcessingKit, state: JobState
    ) -> None:
        """Verify choosing a version takes the book from a preview, queued or running, instead of being refused.

        The editor of a step asks for a preview by itself, so a choice made a moment later finds one.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param state: Whether the preview is queued, or the board has taken it and a task of it is under way.
        :type state: JobState
        """
        actor, book_record, page, first = await ran_geometry(fx_kit)
        async with previewed(fx_kit, actor, book_record, page, state) as preview:
            chosen = await fx_kit.service().choose_version(actor, book_record.id, page.id, Stage.GEOMETRY, first.id)
        announced = [
            (event.job.id, event.job.state) for event in fx_kit.events.published if isinstance(event, JobChanged)
        ]
        expect(chosen.head_version_id == first.id)
        expect(stored_job(fx_kit, preview.id).state is JobState.CANCELLED)
        expect((preview.id, JobState.CANCELLED) in announced)
        assert_expectations()

    @pytest.mark.parametrize('active', [JobKind.RUN_STAGE, JobKind.MEASURE_BOOK])
    async def test_choice_is_still_refused_while_a_run_or_a_measure_is_active(
        self, fx_kit: ProcessingKit, active: JobKind
    ) -> None:
        """Reject the choice while a run or a measure of the book is queued, and leave the job and the stage alone.

        This covers the refusal the fix keeps, and is not a test that fails without the fix.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param active: The kind of the job that is queued.
        :type active: JobKind
        """
        actor, book_record, page, first = await ran_geometry(fx_kit)
        before = await stage_of(fx_kit, book_record.id, page, Stage.GEOMETRY)
        job = await fx_kit.parts(fx_kit.uow()).starter.enqueue(
            book_record.id, active, StageRun(stage=Stage.GEOMETRY).to_map()
        )
        with pytest.raises(ConflictError, match=BUSY_REASON):
            await fx_kit.service().choose_version(actor, book_record.id, page.id, Stage.GEOMETRY, first.id)
        expect(stored_job(fx_kit, job.id).state is JobState.QUEUED)
        expect(await stage_of(fx_kit, book_record.id, page, Stage.GEOMETRY) == before)
        assert_expectations()

    async def test_a_version_that_cannot_be_current_does_not_cancel_the_preview(self, fx_kit: ProcessingKit) -> None:
        """Reject a version of another stage as it is refused when the book is free, and keep the preview queued.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, page, first = await ran_geometry(fx_kit)
        preview = await fx_kit.parts(fx_kit.uow()).starter.enqueue(book_record.id, JobKind.PREVIEW_STEP, {})
        with pytest.raises(ConflictError, match=OTHER_STAGE_REASON):
            await fx_kit.service().choose_version(actor, book_record.id, page.id, Stage.CLEANUP, first.id)
        assert stored_job(fx_kit, preview.id).state is JobState.QUEUED

    async def test_old_versions_are_kept_when_a_later_one_is_chosen(self, fx_kit: ProcessingKit) -> None:
        """Verify choosing a version deletes no other version of the page.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, page, first = await ran_geometry(fx_kit)
        await fx_kit.service().choose_version(actor, book_record.id, page.id, Stage.GEOMETRY, first.id)
        versions = await stage_versions(fx_kit, book_record.id, page, None)
        assert versions.total == 2

    @pytest.mark.parametrize(
        ('make_unfit', 'match'),
        [
            (lambda version: evolve(version, state=VersionState.FAILED), 'not ready'),
            (lambda version: evolve(version, scale=VersionScale.PREVIEW), PREVIEW_REASON),
            (lambda version: evolve(version, stage=Stage.CLEANUP), OTHER_STAGE_REASON),
        ],
        ids=['failed', 'preview', 'other-stage'],
    )
    async def test_version_that_cannot_be_current_is_a_conflict(
        self, fx_kit: ProcessingKit, make_unfit: Callable[[PageVersion], PageVersion], match: str
    ) -> None:
        """Reject a version that is failed, a preview, or of another stage.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param make_unfit: Function making the version unfit.
        :type make_unfit: Callable[[PageVersion], PageVersion]
        :param match: Text the error says.
        :type match: str
        """
        actor, book_record, page, first = await ran_geometry(fx_kit)
        unfit = evolve(make_unfit(first), id=PageVersionId('fedcba9876543210'))
        await fx_kit.uow().change_book(book_record.id, lambda repos, _book_record: repos.page_versions.add(unfit))
        with pytest.raises(ConflictError, match=match):
            await fx_kit.service().choose_version(actor, book_record.id, page.id, Stage.GEOMETRY, unfit.id)

    async def test_version_of_another_page_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Reject a version that belongs to another page, as if it did not exist.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, _, first = await ran_geometry(fx_kit)
        other, _ = await fx_kit.seed_scan_page(book_record, order_key='a1')
        with pytest.raises(NotFoundError):
            await fx_kit.service().choose_version(actor, book_record.id, other.id, Stage.GEOMETRY, first.id)

    async def test_version_without_a_pyramid_gets_a_job_that_cuts_it(self, fx_kit: ProcessingKit) -> None:
        """Verify a chosen version whose pyramid is not cut queues a ``cut-tiles`` job, since the viewer opens it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, page, first = await ran_geometry(fx_kit)
        untiled = evolve(first, id=PageVersionId('0011223344556677'), tiles_ready=False)
        await fx_kit.uow().change_book(book_record.id, lambda repos, _book_record: repos.page_versions.add(untiled))
        await fx_kit.service().choose_version(actor, book_record.id, page.id, Stage.GEOMETRY, untiled.id)
        assert fx_kit.board.put_jobs[-1].kind is JobKind.CUT_TILES


class TestVersions:
    """Tests for listing and reading the versions of a page."""

    async def test_versions_are_listed_by_stage_and_scale(self, fx_kit: ProcessingKit) -> None:
        """Verify the filter narrows the list, and a version is read by its identifier.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, page, first = await ran_geometry(fx_kit)
        everything = await fx_kit.service().versions(actor, book_record.id, page.id, NO_FILTER, EVERYTHING)
        geometry = await fx_kit.service().versions(
            actor, book_record.id, page.id, VersionFilter(stage=Stage.GEOMETRY, scale=VersionScale.FULL), EVERYTHING
        )
        previews = await fx_kit.service().versions(
            actor, book_record.id, page.id, VersionFilter(scale=VersionScale.PREVIEW), EVERYTHING
        )
        read = await fx_kit.service().version(actor, book_record.id, page.id, first.id)
        expect((everything.total, geometry.total, previews.total) == (2, 1, 0))
        expect(read == first)
        assert_expectations()


class TestVersionsOfAStep:
    """Tests for the step and mark filters of ProcessingService.versions."""

    async def test_each_step_lists_the_version_its_place_in_the_chain_gives(self, fx_kit: ProcessingKit) -> None:
        """Verify two steps of one processor are told apart by their place, and each lists its own version only.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, page, [first, second], [made_first, made_second] = await ran_two_steps(fx_kit)
        service = fx_kit.service()
        by_first = await service.versions(
            actor, book_record.id, page.id, VersionFilter(stage=Stage.GEOMETRY, step_id=first), EVERYTHING
        )
        by_second = await service.versions(
            actor, book_record.id, page.id, VersionFilter(stage=Stage.GEOMETRY, step_id=second), EVERYTHING
        )
        expect(list(by_first.items) == [made_first])
        expect(list(by_second.items) == [made_second])
        expect((by_first.total, by_second.total) == (1, 1))
        assert_expectations()

    async def test_a_step_switched_off_has_no_versions_and_the_next_one_takes_its_place(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a step that is off lists nothing, and the step after it stands first in the chain.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, page, [off, on], [made] = await ran_two_steps(fx_kit, first_on=False)
        service = fx_kit.service()
        of_off = await service.versions(
            actor, book_record.id, page.id, VersionFilter(stage=Stage.GEOMETRY, step_id=off), EVERYTHING
        )
        of_on = await service.versions(
            actor, book_record.id, page.id, VersionFilter(stage=Stage.GEOMETRY, step_id=on), EVERYTHING
        )
        expect(of_off.total == 0)
        expect(list(of_on.items) == [made])
        assert_expectations()

    async def test_the_mark_narrows_the_list_of_a_step_and_of_a_stage(self, fx_kit: ProcessingKit) -> None:
        """Verify only the versions that carry the mark are listed, with the step and without it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, page, [first, _], [made_first, _] = await ran_two_steps(fx_kit)
        await mark_version(fx_kit, book_record.id, made_first, ResultMark.BAD)
        service = fx_kit.service()
        bad_of_step = await service.versions(
            actor,
            book_record.id,
            page.id,
            VersionFilter(stage=Stage.GEOMETRY, step_id=first, mark=ResultMark.BAD),
            EVERYTHING,
        )
        good_of_step = await service.versions(
            actor,
            book_record.id,
            page.id,
            VersionFilter(stage=Stage.GEOMETRY, step_id=first, mark=ResultMark.GOOD),
            EVERYTHING,
        )
        bad_of_stage = await service.versions(
            actor, book_record.id, page.id, VersionFilter(stage=Stage.GEOMETRY, mark=ResultMark.BAD), EVERYTHING
        )
        expect([version.id for version in bad_of_step.items] == [made_first.id])
        expect(good_of_step.total == 0)
        expect([version.id for version in bad_of_stage.items] == [made_first.id])
        assert_expectations()

    async def test_the_window_of_a_step_list_is_cut_and_its_total_is_the_whole(self, fx_kit: ProcessingKit) -> None:
        """Verify the offset and the limit apply to the versions of the step and the total counts every one.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, page, [first, _], _ = await ran_two_steps(fx_kit)
        window = await fx_kit.service().versions(
            actor,
            book_record.id,
            page.id,
            VersionFilter(stage=Stage.GEOMETRY, step_id=first),
            SliceRequest(offset=1, limit=1),
        )
        assert (list(window.items), window.total) == ([], 1)

    async def test_a_step_no_recipe_has_is_not_found_and_a_step_without_its_stage_is_refused(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify an unknown step is not an empty list, and a step needs the stage it belongs to.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, page, [first, _], _ = await ran_two_steps(fx_kit)
        with pytest.raises(NotFoundError):
            await fx_kit.service().versions(
                actor, book_record.id, page.id, VersionFilter(stage=Stage.GEOMETRY, step_id=StepId(uuid4())), EVERYTHING
            )
        with pytest.raises(InvalidParametersError):
            await fx_kit.service().versions(actor, book_record.id, page.id, VersionFilter(step_id=first), EVERYTHING)


class TestStartTiles:
    """Tests for the pyramids a viewer asks for."""

    async def test_job_cuts_the_pyramid_of_a_version_and_announces_it(self, fx_kit: ProcessingKit) -> None:
        """Verify a version without a pyramid gets one from the job, and the viewer is told.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, page, first = await ran_geometry(fx_kit)
        untiled = evolve(first, id=PageVersionId('8899aabbccddeeff'), tiles_ready=False)
        await fx_kit.uow().change_book(book_record.id, lambda repos, _book_record: repos.page_versions.add(untiled))
        await fx_kit.store_files(untiled, book_record.id)
        job = await fx_kit.service().start_tiles(actor, book_record.id, page.id, untiled.id)
        await fx_kit.work_queue()
        cut = await fx_kit.stored_version(book_record.id, untiled.id)
        tiles = BookKeys(book_record.id).version_rendition(cut, Rendition.TILES)
        async with fx_kit.assets.readable(tiles) as directory:
            exists = directory.is_dir()
        expect((cut.tiles_ready, exists) == (True, True))
        expect(stored_job(fx_kit, job.id).state is JobState.SUCCEEDED)
        expect(
            any(
                isinstance(event, PageVersionReady) and event.version.id == untiled.id
                for event in fx_kit.events.published
            )
        )
        assert_expectations()

    async def test_version_that_is_not_ready_is_a_conflict(self, fx_kit: ProcessingKit) -> None:
        """Reject cutting the pyramid of a version that failed.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, page, first = await ran_geometry(fx_kit)
        failed = evolve(first, id=PageVersionId('1122334455667788'), state=VersionState.FAILED)
        await fx_kit.uow().change_book(book_record.id, lambda repos, _book_record: repos.page_versions.add(failed))
        with pytest.raises(ConflictError):
            await fx_kit.service().start_tiles(actor, book_record.id, page.id, failed.id)

    async def test_job_parameters_name_the_versions(self, fx_kit: ProcessingKit) -> None:
        """Verify the job stores the versions it must cut in its parameters.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, page, first = await ran_geometry(fx_kit)
        job = await fx_kit.service().start_tiles(actor, book_record.id, page.id, first.id)
        assert TileCut.from_map(job.params) == TileCut(version_ids=(first.id,))


class TestCollection:
    """Tests for the job that deletes the old versions."""

    async def collectable_book(self, kit: ProcessingKit) -> tuple[Actor, BookRecord, Page, PageVersion, PageVersion]:
        """Seed a book with a current version and an old one that nothing needs, both with files.

        :param kit: What the processing services of the test share.
        :type kit: ProcessingKit
        :returns: The actor, the book, the page, the current version and the old version.
        :rtype: tuple[Actor, BookRecord, Page, PageVersion, PageVersion]
        """
        actor, book_record, page, current = await ran_geometry(kit)
        old = evolve(
            current,
            id=PageVersionId('abcdefabcdefabcd'),
            created_at=EPOCH - timedelta(days=90),
            tiles_ready=False,
        )
        keys = BookKeys(book_record.id)
        async with kit.assets.writable(keys.version_rendition(old, Rendition.FULL_JPEG)) as target:
            target.write_bytes(IMAGE_CONTENT)
        await kit.uow().change_book(book_record.id, lambda repos, _book_record: repos.page_versions.add(old))
        return actor, book_record, page, current, old

    @pytest.mark.parametrize(STATE_ARGUMENT, [JobState.QUEUED, JobState.RUNNING])
    async def test_a_preview_of_the_book_is_cancelled_and_the_collection_is_queued(
        self, fx_kit: ProcessingKit, state: JobState
    ) -> None:
        """Verify a collection takes the book from a preview, queued or running, instead of being refused by it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param state: Whether the preview is queued, or the board has taken it and a task of it is under way.
        :type state: JobState
        """
        actor, book_record, page, _ = await ran_geometry(fx_kit)
        async with previewed(fx_kit, actor, book_record, page, state) as preview:
            job = await fx_kit.service().start_collection(actor, book_record.id)
        expect(job.kind is JobKind.COLLECT_VERSIONS)
        expect(fx_kit.board.put_jobs[-2:] == [preview, job])
        expect(stored_job(fx_kit, preview.id).state is JobState.CANCELLED)
        assert_expectations()

    async def test_old_version_nothing_needs_is_deleted_with_its_files_its_row_and_the_log_of_its_marks(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the collection deletes an old version with its directory and its row.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, page, current, old = await self.collectable_book(fx_kit)

        def mark_old(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Mark the old version bad.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            repos.page_versions.update(evolve(old, mark=ResultMark.BAD))

        await fx_kit.uow().change_book(book_record.id, mark_old)
        job = await fx_kit.service().start_collection(actor, book_record.id)
        await fx_kit.work_queue()
        remaining = (await stage_versions(fx_kit, book_record.id, page, None)).items
        with pytest.raises(NotFoundError):
            async with fx_kit.assets.readable(BookKeys(book_record.id).version_directory(old)):
                pass
        expect(old.id not in {version.id for version in remaining})
        expect(len(remaining) == 2)
        expect(await found_version(fx_kit, book_record.id, current.id) is not None)
        stored = stored_job(fx_kit, job.id)
        expect((stored.state, stored.error) == (JobState.SUCCEEDED, ''))
        assert_expectations()

    async def test_run_removes_the_files_of_a_version_it_left_behind_without_a_request(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the collection a run queues when it ends deletes a version that is no longer current at once.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, page, current, old = await self.collectable_book(fx_kit)
        # The version was made a moment ago, so only the end of a run, and not its age, can clear it
        await fx_kit.uow().change_book(
            book_record.id, lambda repos, _book_record: repos.page_versions.update(evolve(old, created_at=EPOCH))
        )
        await run_stage(fx_kit, actor, book_record, StageRun(stage=Stage.GEOMETRY, page_ids=(page.id,)))
        jobs = fx_kit.board.jobs(book_record.id, active=False)
        gone = await found_version(fx_kit, book_record.id, old.id)
        kept = await fx_kit.stored_version(book_record.id, current.id)
        record = await stage_of(fx_kit, book_record.id, page, Stage.GEOMETRY)
        keys = BookKeys(book_record.id)
        with pytest.raises(NotFoundError):
            async with fx_kit.assets.readable(keys.version_directory(old)):
                pass
        async with fx_kit.assets.readable(keys.version_rendition(current, Rendition.FULL_JPEG)):
            pass
        collections = [job for job in jobs if job.kind is JobKind.COLLECT_VERSIONS]
        expect(all(job.state is JobState.SUCCEEDED for job in collections) and bool(collections))
        expect(gone is None)
        expect(kept.state is VersionState.READY)
        expect(record.head_version_id == current.id)
        expect(
            current.input_id is not None and await found_version(fx_kit, book_record.id, current.input_id) is not None
        )
        assert_expectations()

    async def test_a_preview_is_deleted_with_its_row_like_any_other_version(self, fx_kit: ProcessingKit) -> None:
        """Verify an old preview loses its row and its directory, as a version of a full run does.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, _, _, old = await self.collectable_book(fx_kit)
        preview = evolve(
            old,
            id=PREVIEW_VERSION_ID,
            scale=VersionScale.PREVIEW,
            created_at=EPOCH - timedelta(days=2),
        )
        keys = BookKeys(book_record.id)
        async with fx_kit.assets.writable(keys.version_rendition(preview, Rendition.PREVIEW)) as target:
            target.write_bytes(IMAGE_CONTENT)
        await fx_kit.uow().change_book(book_record.id, lambda repos, _book_record: repos.page_versions.add(preview))
        await fx_kit.service().start_collection(actor, book_record.id)
        await fx_kit.work_queue()
        with pytest.raises(NotFoundError):
            async with fx_kit.assets.readable(keys.version_directory(preview)):
                pass
        expect(await found_version(fx_kit, book_record.id, preview.id) is None)
        # The version of the full run that nothing reads goes in the same collection, so the preview is no exception
        expect(await found_version(fx_kit, book_record.id, old.id) is None)
        assert_expectations()

    async def test_young_preview_is_kept_since_the_editor_still_shows_it(self, fx_kit: ProcessingKit) -> None:
        """Verify a preview younger than its retention period keeps its row and its files.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, _, _, old = await self.collectable_book(fx_kit)
        preview = evolve(
            old,
            id=PREVIEW_VERSION_ID,
            scale=VersionScale.PREVIEW,
            created_at=EPOCH - timedelta(minutes=5),
        )
        keys = BookKeys(book_record.id)
        async with fx_kit.assets.writable(keys.version_rendition(preview, Rendition.PREVIEW)) as target:
            target.write_bytes(IMAGE_CONTENT)
        await fx_kit.uow().change_book(book_record.id, lambda repos, _book_record: repos.page_versions.add(preview))
        await fx_kit.service().start_collection(actor, book_record.id)
        await fx_kit.work_queue()
        async with fx_kit.assets.readable(keys.version_rendition(preview, Rendition.PREVIEW)):
            pass
        assert (await found_version(fx_kit, book_record.id, preview.id)) is not None

    async def test_a_second_request_while_one_is_active_gets_the_active_job(self, fx_kit: ProcessingKit) -> None:
        """Verify a book has one collection at a time.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, _, _, _ = await self.collectable_book(fx_kit)
        first = await fx_kit.service().start_collection(actor, book_record.id)
        second = await fx_kit.service().start_collection(actor, book_record.id)
        assert second.id == first.id

    async def test_collection_marks_the_versions_failed_before_it_removes_their_files(
        self, fx_kit: ProcessingKit, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify a collection that cannot remove a directory leaves its version marked and its row, and fails.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param monkeypatch: Replaces the removal of directories with one that fails.
        :type monkeypatch: pytest.MonkeyPatch
        """
        actor, book_record, _, current, old = await self.collectable_book(fx_kit)

        monkeypatch.setattr(fx_kit.assets, DELETE_PREFIX, AsyncMock(side_effect=OSError(READ_ONLY_DISK)))
        job = await fx_kit.service().start_collection(actor, book_record.id)
        await fx_kit.work_queue()
        marked = await fx_kit.stored_version(book_record.id, old.id)
        stored = stored_job(fx_kit, job.id)
        expect((stored.state, stored.error) == (JobState.FAILED, VERSIONS_LEFT.format(count=1)))
        expect((marked.state, marked.data[VersionData.ERROR]) == (VersionState.FAILED, BEING_COLLECTED))
        expect((await fx_kit.stored_version(book_record.id, current.id)).state is VersionState.READY)
        assert_expectations()

    async def test_the_next_collection_finishes_what_a_failed_one_left(
        self, fx_kit: ProcessingKit, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify the versions a failed collection marked are chosen again and deleted with their files.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param monkeypatch: Replaces the removal of directories with one that fails, and then puts it back.
        :type monkeypatch: pytest.MonkeyPatch
        """
        actor, book_record, _, _, old = await self.collectable_book(fx_kit)
        with monkeypatch.context() as broken:
            broken.setattr(fx_kit.assets, DELETE_PREFIX, AsyncMock(side_effect=OSError(READ_ONLY_DISK)))
            await fx_kit.service().start_collection(actor, book_record.id)
            await fx_kit.work_queue()
        again = await fx_kit.service().start_collection(actor, book_record.id)
        await fx_kit.work_queue()
        with pytest.raises(NotFoundError):
            async with fx_kit.assets.readable(BookKeys(book_record.id).version_directory(old)):
                pass
        expect(await found_version(fx_kit, book_record.id, old.id) is None)
        expect(stored_job(fx_kit, again.id).state is JobState.SUCCEEDED)
        assert_expectations()

    @pytest.mark.parametrize(BATCH_ARG, [1, ProcessingJobs.COLLECTION_BATCH_SIZE], ids=['one-by-one', 'one-batch'])
    async def test_a_version_whose_files_stay_keeps_its_row_and_the_deletions_of_the_others_stand(
        self, fx_kit: ProcessingKit, monkeypatch: pytest.MonkeyPatch, batch_size: int
    ) -> None:
        """Verify one version whose directory cannot be removed neither loses its row nor takes back the others.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param monkeypatch: Sets the size of a batch, and replaces the removal of directories with one that refuses one.
        :type monkeypatch: pytest.MonkeyPatch
        :param batch_size: How many versions a collection deletes before it commits.
        :type batch_size: int
        """
        actor, book_record, _, _, old = await self.collectable_book(fx_kit)
        other = evolve(old, id=OTHER_VERSION_ID, created_at=EPOCH - timedelta(days=80))
        keys = BookKeys(book_record.id)
        async with fx_kit.assets.writable(keys.version_rendition(other, Rendition.FULL_JPEG)) as target:
            target.write_bytes(IMAGE_CONTENT)

        def add_other(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Add the second old version.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            repos.page_versions.add(other)

        await fx_kit.uow().change_book(book_record.id, add_other)
        refused = keys.version_directory(old)
        removing = fx_kit.assets.delete_prefix

        async def refuse_one(prefix: StorageKey) -> None:
            """Remove a directory, except the one of the version that must stay.

            :param prefix: Directory to remove.
            :type prefix: StorageKey
            :raises OSError: If the directory is the refused one.
            """
            if prefix == refused:
                err_msg = REFUSED_DIRECTORY
                raise OSError(err_msg)
            await removing(prefix)

        monkeypatch.setattr(ProcessingJobs, BATCH_SIZE_ATTRIBUTE, batch_size)
        monkeypatch.setattr(fx_kit.assets, DELETE_PREFIX, refuse_one)
        job = await fx_kit.service().start_collection(actor, book_record.id)
        await fx_kit.work_queue()

        stayed = await fx_kit.stored_version(book_record.id, old.id)
        stored = stored_job(fx_kit, job.id)
        async with fx_kit.assets.readable(keys.version_rendition(old, Rendition.FULL_JPEG)):
            pass
        expect(await found_version(fx_kit, book_record.id, other.id) is None)
        expect((stayed.state, stayed.data[VersionData.ERROR]) == (VersionState.FAILED, BEING_COLLECTED))
        expect((stored.state, stored.error) == (JobState.FAILED, VERSIONS_LEFT.format(count=1)))
        assert_expectations()

    async def test_the_input_of_a_version_whose_files_stay_is_kept_with_its_files(
        self, fx_kit: ProcessingKit, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify an input is not deleted before the version that reads it, which stays when its files cannot go.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param monkeypatch: Replaces the removal of directories with one that refuses the reader's.
        :type monkeypatch: pytest.MonkeyPatch
        """
        actor, book_record, _, _, old = await self.collectable_book(fx_kit)
        reader = evolve(old, id=OTHER_VERSION_ID, input_id=old.id)
        keys = BookKeys(book_record.id)
        async with fx_kit.assets.writable(keys.version_rendition(reader, Rendition.FULL_JPEG)) as target:
            target.write_bytes(IMAGE_CONTENT)
        await fx_kit.uow().change_book(book_record.id, lambda repos, _book_record: repos.page_versions.add(reader))
        refused = keys.version_directory(reader)
        removing = fx_kit.assets.delete_prefix

        async def refuse_the_reader(prefix: StorageKey) -> None:
            """Remove a directory, except the one of the reader.

            :param prefix: Directory to remove.
            :type prefix: StorageKey
            :raises OSError: If the directory is the reader's.
            """
            if prefix == refused:
                err_msg = REFUSED_DIRECTORY
                raise OSError(err_msg)
            await removing(prefix)

        monkeypatch.setattr(fx_kit.assets, DELETE_PREFIX, refuse_the_reader)
        job = await fx_kit.service().start_collection(actor, book_record.id)
        await fx_kit.work_queue()

        stored = await fx_kit.stored_version(book_record.id, reader.id)
        async with fx_kit.assets.readable(keys.version_rendition(old, Rendition.FULL_JPEG)):
            pass
        expect(stored.input_id == old.id)
        expect(stored_job(fx_kit, job.id).error == VERSIONS_LEFT.format(count=2))
        assert_expectations()

    async def test_a_version_marked_good_or_commented_keeps_its_files_and_its_row(self, fx_kit: ProcessingKit) -> None:
        """Verify a collection deletes neither the version the user judged Good nor the one with a comment.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, _, _, old = await self.collectable_book(fx_kit)
        keys = BookKeys(book_record.id)
        commented = evolve(old, id=OTHER_VERSION_ID, mark=None, comment='Check the margin.')
        good = evolve(old, mark=ResultMark.GOOD)
        async with fx_kit.assets.writable(keys.version_rendition(commented, Rendition.FULL_JPEG)) as target:
            target.write_bytes(IMAGE_CONTENT)

        def judge(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Mark the old version good and add the commented one.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            repos.page_versions.update(good)
            repos.page_versions.add(commented)

        await fx_kit.uow().change_book(book_record.id, judge)
        await fx_kit.service().start_collection(actor, book_record.id)
        await fx_kit.work_queue()
        for kept in (good, commented):
            async with fx_kit.assets.readable(keys.version_rendition(kept, Rendition.FULL_JPEG)):
                pass
        expect(await found_version(fx_kit, book_record.id, good.id) is not None)
        expect(await found_version(fx_kit, book_record.id, commented.id) is not None)
        assert_expectations()

    async def test_the_versions_of_an_old_chain_go_together(self, fx_kit: ProcessingKit) -> None:
        """Verify a version that only an old version reads goes with it, so no version is left without its input.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, _, _, old = await self.collectable_book(fx_kit)
        reader = evolve(old, id=OTHER_VERSION_ID, input_id=old.id)
        await fx_kit.uow().change_book(book_record.id, lambda repos, _book_record: repos.page_versions.add(reader))
        await fx_kit.service().start_collection(actor, book_record.id)
        await fx_kit.work_queue()
        expect(await found_version(fx_kit, book_record.id, old.id) is None)
        expect(await found_version(fx_kit, book_record.id, reader.id) is None)
        assert_expectations()

    async def test_the_report_counts_the_versions_a_collection_would_delete_and_their_bytes(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the report names the versions and the size of the files of those a collection deletes, and deletes none.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, page, _, old = await self.collectable_book(fx_kit)
        # The run that made the book queued a collection of its own, so the report is judged by the jobs it adds
        before = fx_kit.board.jobs(book_record.id, active=False)
        report = await fx_kit.service().collection_report(actor, book_record.id)
        listed = (await stage_versions(fx_kit, book_record.id, page, None)).items
        jobs = fx_kit.board.jobs(book_record.id, active=False)
        async with fx_kit.assets.readable(BookKeys(book_record.id).version_rendition(old, Rendition.FULL_JPEG)):
            pass
        expect(report == CollectionReport(versions=1, size_bytes=len(IMAGE_CONTENT)))
        expect(old.id in {version.id for version in listed})
        expect({job.id for job in jobs} == {job.id for job in before})
        assert_expectations()

    async def test_the_report_of_a_book_with_nothing_to_clear_is_empty(self, fx_kit: ProcessingKit) -> None:
        """Verify a book whose versions are all current or needed reports no version and no bytes.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, _, _ = await ran_geometry(fx_kit)
        assert await fx_kit.service().collection_report(actor, book_record.id) == CollectionReport(
            versions=0, size_bytes=0
        )

    async def test_the_report_of_a_book_of_another_account_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Verify an account that does not own the book is told it does not exist.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_record = await fx_kit.seed_record()
        stranger = Actor(account_id=(await fx_kit.seed_record())[0].account_id)
        with pytest.raises(NotFoundError):
            await fx_kit.service().collection_report(stranger, book_record.id)

    async def test_a_collection_cancelled_while_it_deletes_a_batch_deletes_no_other_batch(
        self, fx_kit: ProcessingKit, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify a job cancelled during a batch finishes that batch, since a task is never cut short, and stops there.

        Each of the two old versions is a batch of its own, and the cancel lands while the first is being removed, so
        the control raises when the batch is counted and the second is left with its row and its files.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param monkeypatch: Sets the size of a batch, and wraps the removal of directories with one that cancels the job.
        :type monkeypatch: pytest.MonkeyPatch
        """
        actor, book_record, _, _, old = await self.collectable_book(fx_kit)
        other = evolve(old, id=OTHER_VERSION_ID, created_at=EPOCH - timedelta(days=80))
        keys = BookKeys(book_record.id)
        async with fx_kit.assets.writable(keys.version_rendition(other, Rendition.FULL_JPEG)) as target:
            target.write_bytes(IMAGE_CONTENT)
        await fx_kit.uow().change_book(book_record.id, lambda repos, _book_record: repos.page_versions.add(other))
        job = await fx_kit.service().start_collection(actor, book_record.id)
        removing = fx_kit.assets.delete_prefix

        async def remove_and_cancel(prefix: StorageKey) -> None:
            """Remove a directory, and cancel the collection while the batch that holds it is under way.

            :param prefix: Directory to remove.
            :type prefix: StorageKey
            """
            await removing(prefix)
            await fx_kit.board.cancel(job.id)

        monkeypatch.setattr(ProcessingJobs, BATCH_SIZE_ATTRIBUTE, 1)
        monkeypatch.setattr(fx_kit.assets, DELETE_PREFIX, remove_and_cancel)
        await fx_kit.work_queue()
        found = [await found_version(fx_kit, book_record.id, version.id) for version in (old, other)]
        expect(stored_job(fx_kit, job.id).state is JobState.CANCELLED)
        expect(sum(version is None for version in found) == 1)
        assert_expectations()

    async def test_an_old_input_of_a_version_that_stays_is_kept(self, fx_kit: ProcessingKit) -> None:
        """Verify a collection does not clear the input of a current version, so its files stay and it keeps its input.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, _, current, old = await self.collectable_book(fx_kit)
        reader = evolve(
            current, id=PageVersionId('1234123412341234'), input_id=old.id, created_at=EPOCH, stage=Stage.CLEANUP
        )

        def add_reader(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Add a version of the cleanup stage that reads the old version, and make it the current one.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            repos.page_versions.add(reader)
            repos.page_stages.save(
                make_page_stage(page_id=reader.page_id, stage=Stage.CLEANUP, head_version_id=reader.id)
            )

        await fx_kit.uow().change_book(book_record.id, add_reader)
        await fx_kit.service().start_collection(actor, book_record.id)
        await fx_kit.work_queue()
        stored = await fx_kit.stored_version(book_record.id, reader.id)
        untouched = await fx_kit.stored_version(book_record.id, old.id)
        async with fx_kit.assets.readable(BookKeys(book_record.id).version_rendition(old, Rendition.FULL_JPEG)):
            pass
        expect(untouched.id == old.id)
        expect(stored.input_id == old.id)
        assert_expectations()


class TestMapToScan:
    """Tests for mapping points of a version back to the scan."""

    async def test_point_of_a_cropped_and_shifted_version_returns_to_the_scan(self, fx_kit: ProcessingKit) -> None:
        """Verify the chain of transforms leads a point of the last version back to the scan, whatever the steps.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, page, geometry = await ran_geometry(fx_kit)
        assert geometry.input_id is not None
        base = await fx_kit.stored_version(book_record.id, geometry.input_id)
        cropped = evolve(
            base, transform=Transform(kind=TransformKind.CROP, quad=HALF, matrix=(1, 0, -1100, 0, 1, 0, 0, 0, 1))
        )
        shifted = evolve(
            geometry, transform=Transform(kind=TransformKind.CROP, quad=HALF, matrix=(1, 0, -10, 0, 1, -20, 0, 0, 1))
        )

        def move(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Give the two versions their transforms.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            repos.page_versions.update(cropped)
            repos.page_versions.update(shifted)

        await fx_kit.uow().change_book(book_record.id, move)
        [mapped] = await fx_kit.service().map_to_scan(actor, book_record.id, page.id, shifted.id, [Point(x=5, y=7)])
        assert mapped == Point(x=1115, y=27)


async def fail_the_chosen_version(kit: ProcessingKit, book_id: BookId, version: PageVersion) -> None:
    """Store the version as failed, as a run that made it again and failed would.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: Book that holds the version.
    :type book_id: BookId
    :param version: The version a user chose.
    :type version: PageVersion
    """
    await change_versions(kit, book_id, lambda versions: versions.update(evolve(version, state=VersionState.FAILED)))


async def delete_the_page_of_the_version(kit: ProcessingKit, book_id: BookId, version: PageVersion) -> None:
    """Delete the page the version belongs to, as a request of the user does.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: Book that holds the version.
    :type book_id: BookId
    :param version: The version a user chose, which goes with its page.
    :type version: PageVersion
    """
    await kit.uow().change_book(book_id, lambda repos, _book_record: repos.pages.delete(version.page_id))


class TestChooseVersionApplyStep:
    """Tests for the change that makes a chosen version current, which checks the version again inside it."""

    @pytest.mark.parametrize(
        ('change', 'refusal'),
        [
            pytest.param(fail_the_chosen_version, ConflictError, id='version-failed-meanwhile'),
            pytest.param(delete_the_page_of_the_version, NotFoundError, id='page-deleted-meanwhile'),
        ],
    )
    async def test_a_choice_whose_version_changed_while_a_preview_was_cancelled_changes_nothing(
        self,
        fx_kit: ProcessingKit,
        monkeypatch: pytest.MonkeyPatch,
        change: Callable[[ProcessingKit, BookId, PageVersion], Awaitable[None]],
        refusal: type[Exception],
    ) -> None:
        """Verify the version is checked again inside the change, after the cancelling that comes before it.

        The cancelling of the preview is a change of its own, and the use case announces it before its own change
        opens, so the version may have failed, or the page may have gone, in between. The stage is not given the
        version.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param monkeypatch: Fixture that restores the event bus.
        :type monkeypatch: pytest.MonkeyPatch
        :param change: The change another request makes while the preview is cancelled.
        :type change: Callable[[ProcessingKit, BookId, PageVersion], Awaitable[None]]
        :param refusal: What the use case refuses with.
        :type refusal: type[Exception]
        """
        actor, book_record, page, first = await ran_geometry(fx_kit)
        second = evolve(first, id=PageVersionId(CHOSEN_VERSION_ID), params={STRENGTH_PARAMETER: 7})
        await fx_kit.uow().change_book(book_record.id, lambda repos, _book_record: repos.page_versions.add(second))
        await fx_kit.parts(fx_kit.uow()).starter.enqueue(book_record.id, JobKind.PREVIEW_STEP, {})
        change_when_published(fx_kit, monkeypatch, partial(change, fx_kit, book_record.id, second))
        with pytest.raises(refusal):
            await fx_kit.service().choose_version(actor, book_record.id, page.id, Stage.GEOMETRY, second.id)
        record = await found_stage(fx_kit, book_record.id, page, Stage.GEOMETRY)
        assert record is None or record.head_version_id != second.id
