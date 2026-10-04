"""Tests for the current version of a stage, the list of versions, tiles on request, collection and coordinates."""

from datetime import timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

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
from bookreviver.domain.events import PageStageChanged, PageVersionReady
from bookreviver.domain.geometry import Point, Quad, Transform
from bookreviver.domain.ids import PageVersionId, StepId
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import PageStageKey, RecipeDraft, SliceRequest, StageRun, Step, TileCut, VersionFilter
from bookreviver.services.job_runs import JobTracker
from bookreviver.services.processing_jobs import BEING_COLLECTED
from tests.helpers.builders import EPOCH, make_page_stage
from tests.helpers.processing import IMAGE_CONTENT
from tests.helpers.processors import FakeProcessor
from tests.helpers.spreads import run_stage

if TYPE_CHECKING:
    from collections.abc import Callable

    from bookreviver.domain.entities import Actor, Job, Page, PageVersion, Project
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

EVERYTHING: SliceRequest = SliceRequest(limit=100)
NO_FILTER: VersionFilter = VersionFilter()
HALF: Quad = Quad(
    top_left=Point(x=1100, y=0),
    top_right=Point(x=2200, y=0),
    bottom_right=Point(x=2200, y=1561),
    bottom_left=Point(x=1100, y=1561),
)


async def ran_geometry(kit: ProcessingKit) -> tuple[Actor, Project, Page, PageVersion]:
    """Seed a page and run the geometry stage on it once.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The actor, the project, the page and the version the run made.
    :rtype: tuple[Actor, Project, Page, PageVersion]
    """
    actor, project = await kit.seed_project()
    page, _ = await kit.seed_scan_page(project)
    await kit.seed_base_version(page)
    run = StageRun(stage=Stage.GEOMETRY)
    job = await kit.service().start_run(actor, project.id, Stage.GEOMETRY, run)
    await kit.jobs().run_stage(job.id)
    await kit.work_queue()
    record = await kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
    assert record.head_version_id is not None
    return actor, project, page, await kit.stored_version(record.head_version_id)


async def ran_two_steps(
    kit: ProcessingKit, *, first_on: bool = True
) -> tuple[Actor, Project, Page, list[StepId], list[PageVersion]]:
    """Seed a page, save a geometry recipe of two steps of one processor, and run it.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param first_on: Whether the first step is switched on.
    :type first_on: bool
    :returns: The actor, the project, the page, the identifiers of the two steps, and the versions the run made in the
              order of their steps.
    :rtype: tuple[Actor, Project, Page, list[StepId], list[PageVersion]]
    """
    actor, project = await kit.seed_project()
    page, _ = await kit.seed_scan_page(project)
    await kit.seed_base_version(page)
    steps = [Step(processor_key=FakeProcessor.spec.key, enabled=first_on), Step(processor_key=FakeProcessor.spec.key)]
    recipe = await kit.service().save_recipe(actor, project.id, Stage.GEOMETRY, RecipeDraft(name='Two', steps=steps))
    await run_stage(kit, actor, project, StageRun(stage=Stage.GEOMETRY))
    found = await kit.uow().page_versions.list_for_page(page.id)
    made = sorted(
        (version for version in found if version.stage is Stage.GEOMETRY), key=lambda version: version.created_at
    )
    return actor, project, page, [step.step_id for step in recipe.steps], made


async def mark_version(kit: ProcessingKit, version: PageVersion, mark: ResultMark) -> None:
    """Set the mark of a stored version.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param version: The version.
    :type version: PageVersion
    :param mark: The mark to set.
    :type mark: ResultMark
    """
    uow = kit.uow()
    await uow.page_versions.update(evolve(version, mark=mark))
    await uow.commit()


class TestChooseVersion:
    """Tests for ProcessingService.choose_version."""

    async def test_old_version_becomes_current_and_the_later_stages_become_stale(self, fx_kit: ProcessingKit) -> None:
        """Verify choosing an earlier version of the geometry makes it current and marks the cleanup stale.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, first = await ran_geometry(fx_kit)
        second = evolve(first, id=PageVersionId('0123456789abcdef'), params={'strength': 7})
        uow = fx_kit.uow()
        await uow.page_versions.add(second)
        await uow.page_stages.save(make_page_stage(page_id=page.id, stage=Stage.CLEANUP, head_version_id=first.id))
        await uow.commit()
        chosen = await fx_kit.service().choose_version(actor, project.id, page.id, Stage.GEOMETRY, second.id)
        later = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.CLEANUP))
        expect((chosen.head_version_id, chosen.state) == (second.id, StageState.FRESH))
        expect(later.state is StageState.STALE)
        expect(
            any(
                isinstance(event, PageStageChanged) and event.stage.stage is Stage.CLEANUP
                for event in fx_kit.events.published
            )
        )
        assert_expectations()

    async def test_choice_is_refused_while_the_project_is_processing_something(self, fx_kit: ProcessingKit) -> None:
        """Reject the choice of a current version while a job may be reading or deleting versions, and change nothing.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, first = await ran_geometry(fx_kit)
        await fx_kit.service().start_collection(actor, project.id)
        with pytest.raises(ConflictError, match='project is busy'):
            await fx_kit.service().choose_version(actor, project.id, page.id, Stage.GEOMETRY, first.id)

    async def test_old_versions_are_kept_when_a_later_one_is_chosen(self, fx_kit: ProcessingKit) -> None:
        """Verify choosing a version deletes no other version of the page.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, first = await ran_geometry(fx_kit)
        await fx_kit.service().choose_version(actor, project.id, page.id, Stage.GEOMETRY, first.id)
        versions = await fx_kit.uow().page_versions.list_for_stage(page.id, None, None, EVERYTHING)
        assert versions.total == 2

    @pytest.mark.parametrize(
        ('make_unfit', 'match'),
        [
            (lambda version: evolve(version, state=VersionState.FAILED), 'not ready'),
            (lambda version: evolve(version, scale=VersionScale.PREVIEW), 'preview'),
            (lambda version: evolve(version, stage=Stage.CLEANUP), 'another stage'),
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
        actor, project, page, first = await ran_geometry(fx_kit)
        unfit = evolve(make_unfit(first), id=PageVersionId('fedcba9876543210'))
        uow = fx_kit.uow()
        await uow.page_versions.add(unfit)
        await uow.commit()
        with pytest.raises(ConflictError, match=match):
            await fx_kit.service().choose_version(actor, project.id, page.id, Stage.GEOMETRY, unfit.id)

    async def test_version_of_another_page_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Reject a version that belongs to another page, as if it did not exist.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _, first = await ran_geometry(fx_kit)
        other, _ = await fx_kit.seed_scan_page(project, order_key='a1')
        with pytest.raises(NotFoundError):
            await fx_kit.service().choose_version(actor, project.id, other.id, Stage.GEOMETRY, first.id)

    async def test_version_without_a_pyramid_gets_a_job_that_cuts_it(self, fx_kit: ProcessingKit) -> None:
        """Verify a chosen version whose pyramid is not cut queues a ``cut-tiles`` job, since the viewer opens it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, first = await ran_geometry(fx_kit)
        untiled = evolve(first, id=PageVersionId('0011223344556677'), tiles_ready=False)
        uow = fx_kit.uow()
        await uow.page_versions.add(untiled)
        await uow.commit()
        await fx_kit.service().choose_version(actor, project.id, page.id, Stage.GEOMETRY, untiled.id)
        assert fx_kit.recording.enqueued[-1].kind is JobKind.CUT_TILES


class TestVersions:
    """Tests for listing and reading the versions of a page."""

    async def test_versions_are_listed_by_stage_and_scale(self, fx_kit: ProcessingKit) -> None:
        """Verify the filter narrows the list, and a version is read by its identifier.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, first = await ran_geometry(fx_kit)
        everything = await fx_kit.service().versions(actor, project.id, page.id, NO_FILTER, EVERYTHING)
        geometry = await fx_kit.service().versions(
            actor, project.id, page.id, VersionFilter(stage=Stage.GEOMETRY, scale=VersionScale.FULL), EVERYTHING
        )
        previews = await fx_kit.service().versions(
            actor, project.id, page.id, VersionFilter(scale=VersionScale.PREVIEW), EVERYTHING
        )
        read = await fx_kit.service().version(actor, project.id, page.id, first.id)
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
        actor, project, page, [first, second], [made_first, made_second] = await ran_two_steps(fx_kit)
        service = fx_kit.service()
        by_first = await service.versions(
            actor, project.id, page.id, VersionFilter(stage=Stage.GEOMETRY, step_id=first), EVERYTHING
        )
        by_second = await service.versions(
            actor, project.id, page.id, VersionFilter(stage=Stage.GEOMETRY, step_id=second), EVERYTHING
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
        actor, project, page, [off, on], [made] = await ran_two_steps(fx_kit, first_on=False)
        service = fx_kit.service()
        of_off = await service.versions(
            actor, project.id, page.id, VersionFilter(stage=Stage.GEOMETRY, step_id=off), EVERYTHING
        )
        of_on = await service.versions(
            actor, project.id, page.id, VersionFilter(stage=Stage.GEOMETRY, step_id=on), EVERYTHING
        )
        expect(of_off.total == 0)
        expect(list(of_on.items) == [made])
        assert_expectations()

    async def test_the_mark_narrows_the_list_of_a_step_and_of_a_stage(self, fx_kit: ProcessingKit) -> None:
        """Verify only the versions that carry the mark are listed, with the step and without it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, [first, _], [made_first, _] = await ran_two_steps(fx_kit)
        await mark_version(fx_kit, made_first, ResultMark.BAD)
        service = fx_kit.service()
        bad_of_step = await service.versions(
            actor,
            project.id,
            page.id,
            VersionFilter(stage=Stage.GEOMETRY, step_id=first, mark=ResultMark.BAD),
            EVERYTHING,
        )
        good_of_step = await service.versions(
            actor,
            project.id,
            page.id,
            VersionFilter(stage=Stage.GEOMETRY, step_id=first, mark=ResultMark.GOOD),
            EVERYTHING,
        )
        bad_of_stage = await service.versions(
            actor, project.id, page.id, VersionFilter(stage=Stage.GEOMETRY, mark=ResultMark.BAD), EVERYTHING
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
        actor, project, page, [first, _], _ = await ran_two_steps(fx_kit)
        window = await fx_kit.service().versions(
            actor,
            project.id,
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
        actor, project, page, [first, _], _ = await ran_two_steps(fx_kit)
        with pytest.raises(NotFoundError):
            await fx_kit.service().versions(
                actor, project.id, page.id, VersionFilter(stage=Stage.GEOMETRY, step_id=StepId(uuid4())), EVERYTHING
            )
        with pytest.raises(InvalidParametersError):
            await fx_kit.service().versions(actor, project.id, page.id, VersionFilter(step_id=first), EVERYTHING)


class TestStartTiles:
    """Tests for the pyramids a viewer asks for."""

    async def test_job_cuts_the_pyramid_of_a_version_and_announces_it(self, fx_kit: ProcessingKit) -> None:
        """Verify a version without a pyramid gets one from the job, and the viewer is told.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, first = await ran_geometry(fx_kit)
        untiled = evolve(first, id=PageVersionId('8899aabbccddeeff'), tiles_ready=False)
        uow = fx_kit.uow()
        await uow.page_versions.add(untiled)
        await uow.commit()
        await fx_kit.store_files(untiled)
        job = await fx_kit.service().start_tiles(actor, project.id, page.id, untiled.id)
        await fx_kit.jobs().cut_tiles(job.id)
        cut = await fx_kit.stored_version(untiled.id)
        tiles = ProjectKeys(project.id).version_rendition(cut, Rendition.TILES)
        async with fx_kit.assets.readable(tiles) as directory:
            exists = directory.is_dir()
        expect((cut.tiles_ready, exists) == (True, True))
        expect((await fx_kit.uow().jobs.get(job.id)).state is JobState.SUCCEEDED)
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
        actor, project, page, first = await ran_geometry(fx_kit)
        failed = evolve(first, id=PageVersionId('1122334455667788'), state=VersionState.FAILED)
        uow = fx_kit.uow()
        await uow.page_versions.add(failed)
        await uow.commit()
        with pytest.raises(ConflictError):
            await fx_kit.service().start_tiles(actor, project.id, page.id, failed.id)

    async def test_job_parameters_name_the_versions(self, fx_kit: ProcessingKit) -> None:
        """Verify the job stores the versions it must cut in its parameters.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, first = await ran_geometry(fx_kit)
        job = await fx_kit.service().start_tiles(actor, project.id, page.id, first.id)
        assert TileCut.from_map(job.params) == TileCut(version_ids=(first.id,))


class TestCollection:
    """Tests for the job that deletes the old versions."""

    async def collectable_book(self, kit: ProcessingKit) -> tuple[Actor, Project, Page, PageVersion, PageVersion]:
        """Seed a book with a current version and an old one that nothing needs, both with files.

        :param kit: What the processing services of the test share.
        :type kit: ProcessingKit
        :returns: The actor, the project, the page, the current version and the old version.
        :rtype: tuple[Actor, Project, Page, PageVersion, PageVersion]
        """
        actor, project, page, current = await ran_geometry(kit)
        old = evolve(
            current,
            id=PageVersionId('abcdefabcdefabcd'),
            created_at=EPOCH - timedelta(days=90),
            tiles_ready=False,
        )
        keys = ProjectKeys(project.id)
        async with kit.assets.writable(keys.version_rendition(old, Rendition.FULL_JPEG)) as target:
            target.write_bytes(IMAGE_CONTENT)
        uow = kit.uow()
        await uow.page_versions.add(old)
        await uow.commit()
        return actor, project, page, current, old

    async def test_old_version_nothing_needs_loses_its_directory_and_keeps_its_row(self, fx_kit: ProcessingKit) -> None:
        """Verify the collection removes the files of an old version and keeps its row, its settings and its time.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, current, old = await self.collectable_book(fx_kit)
        job = await fx_kit.service().start_collection(actor, project.id)
        await fx_kit.jobs().collect_versions(job.id)
        remaining = (await fx_kit.uow().page_versions.list_for_stage(page.id, None, None, EVERYTHING)).items
        cleared = await fx_kit.uow().page_versions.get(old.id)
        kept = await fx_kit.uow().page_versions.get(current.id)
        with pytest.raises(NotFoundError):
            async with fx_kit.assets.readable(ProjectKeys(project.id).version_directory(old)):
                pass
        expect(len(remaining) == 3)
        expect((cleared.files_removed_at, cleared.state, cleared.tiles_ready) == (EPOCH, VersionState.READY, False))
        expect(cleared.renditions is not None and not cleared.renditions.ready)
        expect((cleared.params, cleared.data, cleared.edit_hash) == (old.params, old.data, old.edit_hash))
        expect((cleared.input_id, cleared.created_at) == (old.input_id, old.created_at))
        expect(not kept.files_removed)
        expect((await fx_kit.uow().jobs.get(job.id)).state is JobState.SUCCEEDED)
        assert_expectations()

    async def test_a_collection_does_not_clear_a_version_twice(self, fx_kit: ProcessingKit) -> None:
        """Verify a version whose files are removed is not chosen again, so its time of removal stays.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _, _, old = await self.collectable_book(fx_kit)
        first = await fx_kit.service().start_collection(actor, project.id)
        await fx_kit.jobs().collect_versions(first.id)
        fx_kit.clock.moment = EPOCH + timedelta(days=1)
        second = await fx_kit.service().start_collection(actor, project.id)
        await fx_kit.jobs().collect_versions(second.id)
        assert (await fx_kit.uow().page_versions.get(old.id)).files_removed_at == EPOCH

    async def test_a_preview_is_still_deleted_with_its_row(self, fx_kit: ProcessingKit) -> None:
        """Verify an old preview loses its row and its directory, since a preview is no result to come back to.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _, _, old = await self.collectable_book(fx_kit)
        preview = evolve(
            old,
            id=PageVersionId('fedcbafedcbafedc'),
            scale=VersionScale.PREVIEW,
            created_at=EPOCH - timedelta(days=2),
        )
        keys = ProjectKeys(project.id)
        async with fx_kit.assets.writable(keys.version_rendition(preview, Rendition.PREVIEW)) as target:
            target.write_bytes(IMAGE_CONTENT)
        uow = fx_kit.uow()
        await uow.page_versions.add(preview)
        await uow.commit()
        job = await fx_kit.service().start_collection(actor, project.id)
        await fx_kit.jobs().collect_versions(job.id)
        with pytest.raises(NotFoundError):
            async with fx_kit.assets.readable(keys.version_directory(preview)):
                pass
        expect(await fx_kit.uow().page_versions.find(preview.id) is None)
        expect(await fx_kit.uow().page_versions.find(old.id) is not None)
        assert_expectations()

    async def test_recent_version_is_kept(self, fx_kit: ProcessingKit) -> None:
        """Verify a version younger than the retention period is not collected, current or not.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        fx_kit.clock.moment = EPOCH - timedelta(days=80)
        actor, project, _, _, old = await self.collectable_book(fx_kit)
        job = await fx_kit.service().start_collection(actor, project.id)
        await fx_kit.jobs().collect_versions(job.id)
        assert (await fx_kit.uow().page_versions.find(old.id)) is not None

    async def test_a_second_request_while_one_is_active_gets_the_active_job(self, fx_kit: ProcessingKit) -> None:
        """Verify a project has one collection at a time.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _, _, _ = await self.collectable_book(fx_kit)
        first = await fx_kit.service().start_collection(actor, project.id)
        second = await fx_kit.service().start_collection(actor, project.id)
        assert second.id == first.id

    async def test_collection_marks_the_versions_failed_before_it_removes_their_files(
        self, fx_kit: ProcessingKit, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify a collection that cannot remove a directory leaves its version marked, so nothing reuses it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param monkeypatch: Replaces the removal of directories with one that fails.
        :type monkeypatch: pytest.MonkeyPatch
        """
        actor, project, _, current, old = await self.collectable_book(fx_kit)

        monkeypatch.setattr(fx_kit.assets, 'delete_prefix', AsyncMock(side_effect=OSError('The disk is read only.')))
        job = await fx_kit.service().start_collection(actor, project.id)
        await fx_kit.jobs().collect_versions(job.id)
        marked = await fx_kit.uow().page_versions.get(old.id)
        expect((await fx_kit.uow().jobs.get(job.id)).state is JobState.FAILED)
        expect((marked.state, marked.data[VersionData.ERROR]) == (VersionState.FAILED, BEING_COLLECTED))
        expect((await fx_kit.uow().page_versions.get(current.id)).state is VersionState.READY)
        assert_expectations()

    async def test_the_next_collection_finishes_what_a_failed_one_left(
        self, fx_kit: ProcessingKit, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify the versions a failed collection marked are chosen again, lose their files and are ready again.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param monkeypatch: Replaces the removal of directories with one that fails, and then puts it back.
        :type monkeypatch: pytest.MonkeyPatch
        """
        actor, project, _, _, old = await self.collectable_book(fx_kit)
        with monkeypatch.context() as broken:
            broken.setattr(fx_kit.assets, 'delete_prefix', AsyncMock(side_effect=OSError('The disk is read only.')))
            failed = await fx_kit.service().start_collection(actor, project.id)
            await fx_kit.jobs().collect_versions(failed.id)
        again = await fx_kit.service().start_collection(actor, project.id)
        await fx_kit.jobs().collect_versions(again.id)
        with pytest.raises(NotFoundError):
            async with fx_kit.assets.readable(ProjectKeys(project.id).version_directory(old)):
                pass
        cleared = await fx_kit.uow().page_versions.get(old.id)
        expect(
            (cleared.state, cleared.files_removed, VersionData.ERROR in cleared.data)
            == (VersionState.READY, True, False)
        )
        expect((await fx_kit.uow().jobs.get(again.id)).state is JobState.SUCCEEDED)
        assert_expectations()

    async def test_a_cancelled_collection_deletes_nothing(
        self, fx_kit: ProcessingKit, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify a job cancelled after it started and before it deletes leaves the versions and the files as they are.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param monkeypatch: Makes the record of the job's progress find the job cancelled.
        :type monkeypatch: pytest.MonkeyPatch
        """

        async def cancelled(self: JobTracker, job: Job, *, done: int, total: int) -> None:
            """Answer as the tracker does for a job that was cancelled.

            :param self: The tracker.
            :type self: JobTracker
            :param job: The running job.
            :type job: Job
            :param done: Steps completed.
            :type done: int
            :param total: Steps in all.
            :type total: int
            """

        actor, project, _, _, old = await self.collectable_book(fx_kit)
        monkeypatch.setattr(JobTracker, 'advance', cancelled)
        job = await fx_kit.service().start_collection(actor, project.id)
        await fx_kit.jobs().collect_versions(job.id)
        kept = await fx_kit.uow().page_versions.get(old.id)
        async with fx_kit.assets.readable(ProjectKeys(project.id).version_rendition(old, Rendition.FULL_JPEG)):
            pass
        assert kept.state is old.state

    async def test_an_old_input_of_a_version_that_stays_is_kept(self, fx_kit: ProcessingKit) -> None:
        """Verify a collection does not clear the input of a recent version, so its files stay and it keeps its input.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _, current, old = await self.collectable_book(fx_kit)
        reader = evolve(
            current, id=PageVersionId('1234123412341234'), input_id=old.id, created_at=EPOCH, stage=Stage.CLEANUP
        )
        uow = fx_kit.uow()
        await uow.page_versions.add(reader)
        await uow.commit()
        job = await fx_kit.service().start_collection(actor, project.id)
        await fx_kit.jobs().collect_versions(job.id)
        stored = await fx_kit.uow().page_versions.get(reader.id)
        untouched = await fx_kit.uow().page_versions.get(old.id)
        async with fx_kit.assets.readable(ProjectKeys(project.id).version_rendition(old, Rendition.FULL_JPEG)):
            pass
        expect(not untouched.files_removed)
        expect(stored.input_id == old.id)
        assert_expectations()


class TestMapToScan:
    """Tests for mapping points of a version back to the scan."""

    async def test_point_of_a_cropped_and_shifted_version_returns_to_the_scan(self, fx_kit: ProcessingKit) -> None:
        """Verify the chain of transforms leads a point of the last version back to the scan, whatever the steps.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, geometry = await ran_geometry(fx_kit)
        assert geometry.input_id is not None
        base = await fx_kit.stored_version(geometry.input_id)
        cropped = evolve(
            base, transform=Transform(kind=TransformKind.CROP, quad=HALF, matrix=(1, 0, -1100, 0, 1, 0, 0, 0, 1))
        )
        shifted = evolve(
            geometry, transform=Transform(kind=TransformKind.CROP, quad=HALF, matrix=(1, 0, -10, 0, 1, -20, 0, 0, 1))
        )
        uow = fx_kit.uow()
        await uow.page_versions.update(cropped)
        await uow.page_versions.update(shifted)
        await uow.commit()
        [mapped] = await fx_kit.service().map_to_scan(actor, project.id, page.id, shifted.id, [Point(x=5, y=7)])
        assert mapped == Point(x=1115, y=27)
