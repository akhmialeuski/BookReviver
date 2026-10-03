"""Tests for making the picture of a version again, whose files a collection removed, and for refusing to."""

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import EditorKind, JobKind, JobState, Rendition, Stage, VersionState
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.geometry import Rotation
from bookreviver.domain.ids import PageVersionId
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import (
    NewPageEdit,
    PageStageKey,
    PageStepKey,
    RecipeDraft,
    SliceRequest,
    StageRun,
    Step,
)
from bookreviver.services.stage_runs import REMAKE_INPUT_CHANGED
from tests.helpers.builders import EPOCH
from tests.helpers.processors import STRENGTH_PARAMETER
from tests.helpers.spreads import head_of
from tests.services.test_processing_versions import ran_geometry

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor, Page, PageVersion, Project
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

EVERYTHING: SliceRequest = SliceRequest(limit=100)
AFTER_RETENTION_DAYS: int = 40
STRONGER: int = 2
EDITED_RECIPE: str = 'Edited'
ROTATION: NewPageEdit = NewPageEdit(kind=EditorKind.ROTATION, geometry=Rotation(degrees=1.5))


async def run_geometry(kit: ProcessingKit, actor: Actor, project: Project) -> None:
    """Run the geometry stage on every page of the book and wait for the work it queues.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: Owner of the book.
    :type actor: Actor
    :param project: The book.
    :type project: Project
    """
    run = await kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
    await kit.jobs().run_stage(run.id)
    await kit.work_queue()


async def collected_book(kit: ProcessingKit) -> tuple[Actor, Project, Page, PageVersion, PageVersion]:
    """Run the geometry stage twice with different parameters and collect the old version of the first run.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The actor, the project, the page, the first version, which lost its files, and the current version.
    :rtype: tuple[Actor, Project, Page, PageVersion, PageVersion]
    """
    actor, project, page, first = await ran_geometry(kit)
    fake = kit.fake.spec.key
    stronger = RecipeDraft(name='Stronger', steps=[Step(processor_key=fake, params={STRENGTH_PARAMETER: STRONGER})])
    await kit.service().save_recipe(actor, project.id, Stage.GEOMETRY, stronger)
    run = await kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
    await kit.jobs().run_stage(run.id)
    await kit.work_queue()
    head = (await kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))).head_version_id
    assert head is not None
    current = await kit.stored_version(head)
    kit.clock.moment = EPOCH + timedelta(days=AFTER_RETENTION_DAYS)
    collection = await kit.service().start_collection(actor, project.id)
    await kit.jobs().collect_versions(collection.id)
    return actor, project, page, await kit.uow().page_versions.get(first.id), current


class TestRemake:
    """Tests for ProcessingService.start_remake and the run it queues."""

    async def test_remake_gives_the_same_version_its_files_again_and_makes_it_current(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a version whose files were removed is made again under its identifier, without a new row.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, first, current = await collected_book(fx_kit)
        assert first.files_removed
        assert first.id != current.id
        before = (await fx_kit.uow().page_versions.list_for_stage(page.id, None, None, EVERYTHING)).total

        job = await fx_kit.service().start_remake(actor, project.id, page.id, first.id)
        await fx_kit.jobs().run_stage(job.id)

        remade = await fx_kit.uow().page_versions.get(first.id)
        record = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        after = (await fx_kit.uow().page_versions.list_for_stage(page.id, None, None, EVERYTHING)).total
        async with fx_kit.assets.readable(ProjectKeys(project.id).version_rendition(remade, Rendition.FULL_JPEG)):
            pass
        expect((job.kind, (await fx_kit.uow().jobs.get(job.id)).state) == (JobKind.RUN_STAGE, JobState.SUCCEEDED))
        expect(
            (remade.state, remade.files_removed, remade.renditions is not None and remade.renditions.ready)
            == (VersionState.READY, False, True)
        )
        expect((remade.params, remade.input_id, remade.created_at) == (first.params, first.input_id, first.created_at))
        expect(record.head_version_id == first.id)
        expect(after == before)
        assert_expectations()

    async def test_remake_of_a_version_that_has_its_files_is_refused(self, fx_kit: ProcessingKit) -> None:
        """Verify there is nothing to make again for a version that still has its picture.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, current = await ran_geometry(fx_kit)
        with pytest.raises(ConflictError):
            await fx_kit.service().start_remake(actor, project.id, page.id, current.id)

    async def test_a_version_without_files_cannot_be_chosen_without_making_it_again(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify choosing a version whose files were removed is refused, since there is no image to show.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, first, _ = await collected_book(fx_kit)
        with pytest.raises(ConflictError):
            await fx_kit.service().choose_version(actor, project.id, page.id, Stage.GEOMETRY, first.id)

    async def test_remake_of_a_version_of_another_page_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Verify the version must belong to the page in the address.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, first, _ = await collected_book(fx_kit)
        other, _ = await fx_kit.seed_scan_page(project, order_key='a1')
        assert other.id != page.id
        with pytest.raises(NotFoundError):
            await fx_kit.service().start_remake(actor, project.id, other.id, first.id)

    async def test_remake_over_another_input_fails_the_job_and_makes_nothing(self, fx_kit: ProcessingKit) -> None:
        """Verify a version made from a result the earlier stage no longer has is not made again from another one.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, first, current = await collected_book(fx_kit)
        # A cleanup result that read the first geometry result, while the current one is another
        reader = evolve(
            first,
            id=PageVersionId('1234123412341234'),
            stage=Stage.CLEANUP,
            input_id=first.id,
            created_at=EPOCH,
        )
        uow = fx_kit.uow()
        await uow.page_versions.add(reader)
        await uow.commit()
        before = (await fx_kit.uow().page_versions.list_for_stage(page.id, None, None, EVERYTHING)).total

        job = await fx_kit.service().start_remake(actor, project.id, page.id, reader.id)
        await fx_kit.jobs().run_stage(job.id)

        stored = await fx_kit.uow().jobs.get(job.id)
        after = (await fx_kit.uow().page_versions.list_for_stage(page.id, None, None, EVERYTHING)).total
        record = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        expect((stored.state, stored.error) == (JobState.FAILED, REMAKE_INPUT_CHANGED))
        expect(after == before)
        expect(record.head_version_id == current.id)
        assert_expectations()

    async def test_remake_of_a_version_with_a_manual_edit_reads_the_edit_of_its_step(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a version made with a manual edit is made again, since the run takes the step of the recipe.

        The edit belongs to the identifier of the step, so a step made anew from the stored parameters would read no
        edit, and its version would have another identifier than the one asked for.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        page, _ = await fx_kit.seed_scan_page(project)
        await fx_kit.seed_base_version(page)
        step = Step(processor_key=fx_kit.fake.spec.key, params={STRENGTH_PARAMETER: 1})
        await fx_kit.service().save_recipe(
            actor, project.id, Stage.GEOMETRY, RecipeDraft(name=EDITED_RECIPE, steps=[step])
        )
        await fx_kit.edits().save(actor, project.id, PageStepKey(page.id, Stage.GEOMETRY, step.step_id), ROTATION, None)
        await run_geometry(fx_kit, actor, project)
        first = await head_of(fx_kit, page, Stage.GEOMETRY)
        # The same step with another parameter, as the settings of a step are changed in the interface
        stronger = evolve(step, params={STRENGTH_PARAMETER: STRONGER})
        await fx_kit.service().save_recipe(
            actor, project.id, Stage.GEOMETRY, RecipeDraft(name=EDITED_RECIPE, steps=[stronger])
        )
        await run_geometry(fx_kit, actor, project)
        fx_kit.clock.moment = EPOCH + timedelta(days=AFTER_RETENTION_DAYS)
        collection = await fx_kit.service().start_collection(actor, project.id)
        await fx_kit.jobs().collect_versions(collection.id)
        assert (await fx_kit.uow().page_versions.get(first.id)).files_removed

        job = await fx_kit.service().start_remake(actor, project.id, page.id, first.id)
        await fx_kit.jobs().run_stage(job.id)

        remade = await fx_kit.uow().page_versions.get(first.id)
        record = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        expect(first.edit_hash != '')
        expect((await fx_kit.uow().jobs.get(job.id)).state is JobState.SUCCEEDED)
        expect((remade.files_removed, remade.edit_hash) == (False, first.edit_hash))
        expect(record.head_version_id == first.id)
        assert_expectations()
