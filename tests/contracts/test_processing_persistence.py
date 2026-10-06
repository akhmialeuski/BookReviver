"""Contract of the persistence ports of processing: recipes, stage records, the state and history of steps and page versions.

Every test runs against each adapter registered in the conftest, the in-memory one and the SQL one, so both keep the
promises of the ports: the keys they check, the actions of the foreign keys, and the queries a run and a collection
make.
"""

from datetime import timedelta
from operator import attrgetter
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from attrs import evolve

from bookreviver.domain.entities import PageEdit
from bookreviver.domain.enums import (
    AppliesTo,
    ChangeSource,
    PageOrigin,
    ResultMark,
    ReviewReason,
    Stage,
    StageState,
    VersionScale,
    VersionState,
)
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.geometry import Line, Point
from bookreviver.domain.ids import ChangeBatchId, PageId, PageStepChangeId, PageVersionId, ProjectId, StepId
from bookreviver.domain.stage_summaries import StepTally
from bookreviver.domain.values import PageStageKey, PageStepKey, SliceRequest, Step
from tests.helpers.builders import (
    DESKEW_STEP_ID,
    EPOCH,
    make_job,
    make_page,
    make_page_edit,
    make_page_stage,
    make_page_step_change,
    make_page_step_state,
    make_page_version,
    make_project,
    make_recipe,
    new_account_id,
)

if TYPE_CHECKING:
    from bookreviver.domain.entities import Page, PageVersion
    from bookreviver.ports.persistence import UnitOfWork
    from tests.contracts.conftest import OwnerFactory, UnitOfWorkFactory

pytestmark = pytest.mark.anyio

# A version created long before the moment a collection looks back to, and one created after it
OLD: timedelta = timedelta(days=60)
RECENT: timedelta = timedelta(days=1)
VARIANT_NAME: str = 'Variant'
# Order key of a second page of the same book, after the 'a0' a stored page gets
SECOND_ORDER_KEY: str = 'a1'
NOW = EPOCH + timedelta(days=100)
FULL_CUTOFF = NOW - timedelta(days=30)
PREVIEW_CUTOFF = NOW - timedelta(hours=1)


async def _store_page(uow_factory: UnitOfWorkFactory, new_owner: OwnerFactory) -> tuple[ProjectId, PageId]:
    """Store a project with one page and commit.

    :param uow_factory: Function opening a new unit of work of the backend under test.
    :type uow_factory: UnitOfWorkFactory
    :param new_owner: Function creating an account the backend accepts as an owner.
    :type new_owner: OwnerFactory
    :returns: The identifiers of the project and its page.
    :rtype: tuple[ProjectId, PageId]
    """
    uow = await uow_factory()
    project = make_project(owner_id=await new_owner())
    page = make_page(project_id=project.id)
    await uow.projects.add(project)
    await uow.pages.add(page)
    await uow.commit()
    return project.id, page.id


def _blank_page(project_id: ProjectId, order_key: str) -> Page:
    """Build a page that has an image, a blank leaf, since a placeholder has none.

    :param project_id: Project owning the page.
    :type project_id: ProjectId
    :param order_key: Order key placing the page in the book.
    :type order_key: str
    :returns: The page.
    :rtype: Page
    """
    return evolve(make_page(project_id=project_id, order_key=order_key), origin=PageOrigin.BLANK)


async def _add_marked_head(uow: UnitOfWork, page: Page) -> PageVersionId:
    """Store a base version of a page and a version of the geometry stage marked for review.

    :param uow: Unit of work to add to, in which the page is stored.
    :type uow: UnitOfWork
    :param page: The page.
    :type page: Page
    :returns: The identifier of the marked version.
    :rtype: PageVersionId
    """
    base = make_page_version(page_id=page.id)
    head = evolve(_version(base), review=ReviewReason.LOW_CONFIDENCE)
    await uow.page_versions.add_many([base, head])
    return head.id


def _version(
    base: PageVersion,
    *,
    stage: Stage = Stage.GEOMETRY,
    scale: VersionScale = VersionScale.FULL,
    edit_hash: str = '',
    tiles_ready: bool = False,
) -> PageVersion:
    """Build a ready version of a base version, which is old enough for a collection to delete.

    :param base: Base version of the page, stored before the version, which the new one is computed from.
    :type base: PageVersion
    :param stage: Stage of the version.
    :type stage: Stage
    :param scale: Scale of the run.
    :type scale: VersionScale
    :param edit_hash: Hash of the manual edit the step read.
    :type edit_hash: str
    :param tiles_ready: Whether the pyramid of the version is cut.
    :type tiles_ready: bool
    :returns: A ready version with a fresh identifier, created two months after the epoch.
    :rtype: PageVersion
    """
    return evolve(
        make_page_version(page_id=base.page_id),
        stage=stage,
        input_id=base.id,
        state=VersionState.READY,
        scale=scale,
        edit_hash=edit_hash,
        tiles_ready=tiles_ready,
        created_at=EPOCH + OLD,
    )


class TestRecipeRepository:
    """Tests for the recipes of a project."""

    async def test_stage_has_one_active_recipe(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Reject a second active recipe of a stage, which two requests switching the recipe could both store.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        uow = await fx_uow_factory()
        project = make_project(owner_id=await fx_new_owner())
        await uow.projects.add(project)
        await uow.recipes.add(make_recipe(project_id=project.id, active=True))
        with pytest.raises(ConflictError):
            await uow.recipes.add(make_recipe(project_id=project.id, name='Other', active=True))

    async def test_variants_and_other_stages_may_be_added_beside_the_active_recipe(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the one active recipe is per stage, and any number of variants stand beside it.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        uow = await fx_uow_factory()
        project = make_project(owner_id=await fx_new_owner())
        await uow.projects.add(project)
        active = make_recipe(project_id=project.id, active=True)
        variant = make_recipe(project_id=project.id, name=VARIANT_NAME, minutes=1)
        other_stage = make_recipe(project_id=project.id, stage=Stage.CLEANUP, active=True)
        await uow.recipes.add_many([variant, active, other_stage])
        await uow.commit()
        listed = await (await fx_uow_factory()).recipes.list_for_stage(project.id, Stage.GEOMETRY)
        assert [recipe.id for recipe in listed] == [active.id, variant.id]

    async def test_find_active_returns_the_active_recipe_or_none(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the active recipe of a stage is found, and a stage without one gives None.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        uow = await fx_uow_factory()
        project = make_project(owner_id=await fx_new_owner())
        await uow.projects.add(project)
        active = make_recipe(project_id=project.id, active=True)
        await uow.recipes.add(active)
        assert (
            await uow.recipes.find_active(project.id, Stage.GEOMETRY),
            await uow.recipes.find_active(project.id, Stage.CLEANUP),
        ) == (active, None)

    async def test_list_active_returns_the_active_recipe_of_each_stage_in_pipeline_order(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify variants and the recipes of another project are left out, and the stages keep the pipeline order.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        project, other = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        cleanup = make_recipe(project_id=project.id, stage=Stage.CLEANUP, active=True)
        geometry = make_recipe(project_id=project.id, active=True)
        uow = await fx_uow_factory()
        for owned in (project, other):
            await uow.projects.add(owned)
        for recipe in (
            cleanup,
            geometry,
            make_recipe(project_id=project.id, name=VARIANT_NAME, minutes=1),
            make_recipe(project_id=other.id, active=True),
        ):
            await uow.recipes.add(recipe)
        await uow.commit()
        recipes = (await fx_uow_factory()).recipes
        assert [recipe.id for recipe in await recipes.list_active(project.id)] == [geometry.id, cleanup.id]

    async def test_steps_survive_the_store(self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory) -> None:
        """Verify a recipe reads back with its steps, their parameters, identifiers and conditions, and the switches.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        uow = await fx_uow_factory()
        project = make_project(owner_id=await fx_new_owner())
        plain = make_recipe(project_id=project.id)
        other = Step(processor_key='geometry.other', params={}, enabled=False, applies_to=AppliesTo.PICTURES)
        recipe = evolve(plain, steps=(*plain.steps, other, evolve(plain.steps[0], step_id=StepId(uuid4()))))
        await uow.projects.add(project)
        await uow.recipes.add(recipe)
        await uow.commit()
        assert await (await fx_uow_factory()).recipes.get(recipe.id) == recipe

    async def test_recipe_of_a_missing_project_is_not_found(self, fx_uow_factory: UnitOfWorkFactory) -> None:
        """Reject a recipe whose project is not stored.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        """
        uow = await fx_uow_factory()
        with pytest.raises(NotFoundError):
            await uow.recipes.add(make_recipe(project_id=ProjectId(new_account_id())))

    async def test_deleting_a_recipe_leaves_its_pages_without_it(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the stage record of a page keeps its row and loses the recipe that is deleted.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = await fx_uow_factory()
        recipe = make_recipe(project_id=project_id, active=True)
        await uow.recipes.add(recipe)
        await uow.page_stages.save(make_page_stage(page_id=page_id, recipe_id=recipe.id))
        await uow.commit()
        uow = await fx_uow_factory()
        await uow.recipes.delete(recipe.id)
        await uow.commit()
        kept = await (await fx_uow_factory()).page_stages.get(PageStageKey(page_id, Stage.GEOMETRY))
        assert kept.recipe_id is None

    async def test_deleting_the_project_deletes_its_recipes(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the recipes go with their project.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, _ = await _store_page(fx_uow_factory, fx_new_owner)
        uow = await fx_uow_factory()
        recipe = make_recipe(project_id=project_id)
        await uow.recipes.add(recipe)
        await uow.commit()
        uow = await fx_uow_factory()
        await uow.projects.delete(project_id)
        await uow.commit()
        with pytest.raises(NotFoundError):
            await (await fx_uow_factory()).recipes.get(recipe.id)


class TestPageStageRepository:
    """Tests for the current version of each stage of each page."""

    async def test_save_stores_a_record_and_replaces_it(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a record is stored by the first save and replaced by the second, which is how a run advances it.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = await fx_uow_factory()
        await uow.page_stages.save(make_page_stage(page_id=page_id))
        stale = make_page_stage(page_id=page_id, state=StageState.STALE)
        await uow.page_stages.save(stale)
        await uow.commit()
        assert await (await fx_uow_factory()).page_stages.find(PageStageKey(page_id, Stage.GEOMETRY)) == stale

    async def test_find_returns_none_for_a_stage_that_has_not_run(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a missing record is None for find and a NotFoundError for get.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = await fx_uow_factory()
        key = PageStageKey(page_id, Stage.CLEANUP)
        assert await uow.page_stages.find(key) is None
        with pytest.raises(NotFoundError):
            await uow.page_stages.get(key)

    async def test_record_of_a_missing_page_is_not_found(self, fx_uow_factory: UnitOfWorkFactory) -> None:
        """Reject a record whose page is not stored.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        """
        uow = await fx_uow_factory()
        with pytest.raises(NotFoundError):
            await uow.page_stages.save(make_page_stage(page_id=PageId(new_account_id())))

    async def test_list_for_page_follows_the_order_of_the_stages(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the records of a page come in pipeline order, whatever order they were saved in.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = await fx_uow_factory()
        for stage in (Stage.CLEANUP, Stage.PAGE_SPLIT, Stage.GEOMETRY):
            await uow.page_stages.save(make_page_stage(page_id=page_id, stage=stage))
        assert [record.stage for record in await uow.page_stages.list_for_page(page_id)] == [
            Stage.PAGE_SPLIT,
            Stage.GEOMETRY,
            Stage.CLEANUP,
        ]

    async def test_list_for_pages_reads_the_records_of_the_given_pages_only(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify one read gives the records of several pages in pipeline order, and none of the other pages.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, first = await _store_page(fx_uow_factory, fx_new_owner)
        _, second = await _store_page(fx_uow_factory, fx_new_owner)
        _, other = await _store_page(fx_uow_factory, fx_new_owner)
        uow = await fx_uow_factory()
        for page_id in (first, second, other):
            for stage in (Stage.GEOMETRY, Stage.PAGE_SPLIT):
                await uow.page_stages.save(make_page_stage(page_id=page_id, stage=stage))
        found = await uow.page_stages.list_for_pages([first, second])
        by_page = {page_id: [r.stage for r in found if r.page_id == page_id] for page_id in (first, second, other)}
        assert by_page == {
            first: [Stage.PAGE_SPLIT, Stage.GEOMETRY],
            second: [Stage.PAGE_SPLIT, Stage.GEOMETRY],
            other: [],
        }

    async def test_head_version_and_recipe_are_checked(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Reject a record that names a version or a recipe that is not stored.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = await fx_uow_factory()
        with pytest.raises(NotFoundError):
            await uow.page_stages.save(make_page_stage(page_id=page_id, head_version_id=PageVersionId('0' * 16)))

    async def test_queries_by_recipe_and_by_stage_of_a_project(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the records of a recipe and the records of a stage of a project are found, and only those.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        other_project_id, other_page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = await fx_uow_factory()
        recipe = make_recipe(project_id=project_id, active=True)
        await uow.recipes.add(recipe)
        mine = make_page_stage(page_id=page_id, recipe_id=recipe.id)
        await uow.page_stages.save(mine)
        await uow.page_stages.save(make_page_stage(page_id=page_id, stage=Stage.CLEANUP))
        await uow.page_stages.save(make_page_stage(page_id=other_page_id))
        await uow.commit()
        uow = await fx_uow_factory()
        assert (
            await uow.page_stages.list_for_recipe(recipe.id),
            await uow.page_stages.list_for_project_stage(project_id, Stage.GEOMETRY),
            await uow.page_stages.list_for_project_stage(other_project_id, Stage.CLEANUP),
        ) == ([mine], [mine], [])

    async def test_head_ids_lists_the_current_versions_of_a_project(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the identifiers of the head versions of the project's records are listed once each.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = await fx_uow_factory()
        base = make_page_version(page_id=page_id)
        version = _version(base)
        await uow.page_versions.add_many([base, version])
        await uow.page_stages.save(make_page_stage(page_id=page_id, head_version_id=version.id))
        await uow.page_stages.save(make_page_stage(page_id=page_id, stage=Stage.CLEANUP))
        assert set(await uow.page_stages.head_ids(project_id)) == {version.id}

    async def test_tally_counts_the_records_of_each_stage_by_state_and_review(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify one grouped count gives each stage its states and marks, and leaves out what does not count.

        A mark on a failed page, a placeholder and the pages of another project are not counted.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        project, other = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        uow = await fx_uow_factory()
        for owned in (project, other):
            await uow.projects.add(owned)
        marked, stale, failed = (_blank_page(project.id, f'a{number}') for number in range(3))
        placeholder = make_page(project_id=project.id, order_key='b0')
        elsewhere = _blank_page(other.id, 'a0')
        await uow.pages.add_many([marked, stale, failed, placeholder, elsewhere])
        marked_head = await _add_marked_head(uow, marked)
        failed_head = await _add_marked_head(uow, failed)
        for record in (
            make_page_stage(page_id=marked.id, head_version_id=marked_head),
            make_page_stage(page_id=stale.id, state=StageState.STALE),
            make_page_stage(page_id=failed.id, head_version_id=failed_head, state=StageState.FAILED),
            make_page_stage(page_id=placeholder.id),
            make_page_stage(page_id=marked.id, stage=Stage.CLEANUP),
            make_page_stage(page_id=elsewhere.id),
        ):
            await uow.page_stages.save(record)
        await uow.commit()
        repository = (await fx_uow_factory()).page_stages
        tallies = await repository.tally({project.id})
        counts = {(tally.stage, tally.fresh, tally.stale, tally.failed, tally.review, tally.check) for tally in tallies}
        assert counts == {(Stage.GEOMETRY, 1, 1, 1, 1, 3), (Stage.CLEANUP, 1, 0, 0, 0, 0)}
        assert {tally.project_id for tally in tallies} == {project.id}
        assert await repository.tally(set()) == []

    async def test_tally_counts_a_page_both_stale_and_marked_once_in_check(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a page that is stale and marked adds to check once, and a fresh page with no mark adds nothing.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        both, clean = (_blank_page(project.id, f'a{number}') for number in range(2))
        await uow.pages.add_many([both, clean])
        head_id = await _add_marked_head(uow, both)
        await uow.page_stages.save(make_page_stage(page_id=both.id, head_version_id=head_id, state=StageState.STALE))
        await uow.page_stages.save(make_page_stage(page_id=clean.id))
        await uow.commit()
        [tally] = await (await fx_uow_factory()).page_stages.tally({project.id})
        assert (tally.fresh, tally.stale, tally.review, tally.check) == (1, 1, 1, 1)

    async def test_step_tally_counts_the_pages_stopped_at_each_step(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the pages a run stopped at a step are counted by step, and partial in the tally of the stage.

        A page run through every step, a failed record, a placeholder and the pages of another project are left out.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        project, other = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        uow = await fx_uow_factory()
        for owned in (project, other):
            await uow.projects.add(owned)
        first, second, whole, failed = (_blank_page(project.id, f'a{number}') for number in range(4))
        placeholder = make_page(project_id=project.id, order_key='b1')
        elsewhere = _blank_page(other.id, 'a1')
        await uow.pages.add_many([first, second, whole, failed, placeholder, elsewhere])
        for page, through_step, state in (
            (first, 0, StageState.FRESH),
            (second, 0, StageState.STALE),
            (whole, None, StageState.FRESH),
            (failed, 1, StageState.FAILED),
            (placeholder, 0, StageState.FRESH),
            (elsewhere, 0, StageState.FRESH),
        ):
            record = make_page_stage(page_id=page.id, state=state)
            await uow.page_stages.save(evolve(record, through_step=through_step))
        await uow.commit()
        repository = (await fx_uow_factory()).page_stages
        [tally] = await repository.tally({project.id})
        assert await repository.step_tally(project.id) == [StepTally(stage=Stage.GEOMETRY, through_step=0, pages=2)]
        assert tally.partial == 2

    async def test_deleting_the_head_version_leaves_the_record_without_it(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the record keeps its row when its head version is deleted.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = await fx_uow_factory()
        base = make_page_version(page_id=page_id)
        version = _version(base)
        await uow.page_versions.add_many([base, version])
        await uow.page_stages.save(make_page_stage(page_id=page_id, head_version_id=version.id))
        await uow.commit()
        uow = await fx_uow_factory()
        await uow.page_versions.delete(version.id)
        await uow.commit()
        kept = await (await fx_uow_factory()).page_stages.get(PageStageKey(page_id, Stage.GEOMETRY))
        assert kept.head_version_id is None

    async def test_deleting_the_page_deletes_its_records_states_and_history(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the stage records, the step states and the history of the steps go with their page.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = await fx_uow_factory()
        await uow.page_stages.save(make_page_stage(page_id=page_id))
        await uow.page_step_states.save(make_page_step_state(page_id=page_id, edit=make_page_edit(page_id=page_id)))
        await uow.page_step_changes.add(make_page_step_change(page_id=page_id))
        await uow.commit()
        uow = await fx_uow_factory()
        await uow.pages.delete(page_id)
        await uow.commit()
        uow = await fx_uow_factory()
        assert (
            await uow.page_stages.find(PageStageKey(page_id, Stage.GEOMETRY)),
            await uow.page_step_states.list_for_page(page_id),
            await uow.page_step_changes.list_for_page(page_id),
        ) == (None, [], [])


class TestPageStepStateRepository:
    """Tests for the settings and the manual edits of the steps of the pages."""

    async def test_save_stores_a_state_and_replaces_it(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a second save of the same step on the same page and stage replaces the state.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = await fx_uow_factory()
        await uow.page_step_states.save(make_page_step_state(page_id=page_id, params={'max_angle_deg': 3}))
        replacement = make_page_step_state(page_id=page_id, params={'max_angle_deg': 7})
        await uow.page_step_states.save(replacement)
        await uow.commit()
        found = await (await fx_uow_factory()).page_step_states.find(
            PageStepKey(page_id, Stage.GEOMETRY, DESKEW_STEP_ID)
        )
        assert found == replacement

    async def test_settings_and_edit_survive_the_store_together(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the fields a page changes and its manual edit read back, and a state may hold either alone.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        both = make_page_step_state(
            page_id=page_id, params={'max_angle_deg': 3, 'method': 'projection'}, edit=make_page_edit(page_id=page_id)
        )
        settings_only = make_page_step_state(page_id=page_id, stage=Stage.CLEANUP, params={'threshold': 0.5})
        edit_only = make_page_step_state(
            page_id=page_id, stage=Stage.LAYOUT, edit=make_page_edit(page_id=page_id, stage=Stage.LAYOUT)
        )
        uow = await fx_uow_factory()
        for state in (both, settings_only, edit_only):
            await uow.page_step_states.save(state)
        await uow.commit()
        states = (await fx_uow_factory()).page_step_states
        assert [await states.get(state.key) for state in (both, settings_only, edit_only)] == [
            both,
            settings_only,
            edit_only,
        ]

    async def test_geometry_and_mask_survive_the_store(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the split line a user drew reads back as the same line in the edit of a state.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        geometry = Line(start=Point(x=1100.5, y=0), end=Point(x=1104, y=1561))
        edit = PageEdit(
            page_id=page_id,
            stage=Stage.PAGE_SPLIT,
            step_id=StepId(uuid4()),
            kind=geometry.editor,
            geometry=geometry,
            mask_key=None,
            edit_hash=PageEdit.hash_of(geometry, None),
            updated_at=EPOCH,
        )
        state = make_page_step_state(page_id=page_id, stage=Stage.PAGE_SPLIT, step_id=edit.step_id, edit=edit)
        uow = await fx_uow_factory()
        await uow.page_step_states.save(state)
        await uow.commit()
        assert await (await fx_uow_factory()).page_step_states.get(state.key) == state

    async def test_list_for_page_filters_by_stage(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the states of one stage are listed apart from those of the page's other stages.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = await fx_uow_factory()
        geometry = make_page_step_state(page_id=page_id, params={'max_angle_deg': 3})
        cleanup = evolve(geometry, stage=Stage.CLEANUP)
        await uow.page_step_states.save(cleanup)
        await uow.page_step_states.save(geometry)
        assert (
            await uow.page_step_states.list_for_page(page_id, Stage.GEOMETRY),
            await uow.page_step_states.list_for_page(page_id),
        ) == ([geometry], [geometry, cleanup])

    async def test_two_steps_of_one_processor_keep_two_states(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify states of two steps on one page and stage are stored apart and found each by its step.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        first = make_page_step_state(page_id=page_id, params={'max_angle_deg': 1})
        second = make_page_step_state(page_id=page_id, step_id=StepId(uuid4()), params={'max_angle_deg': 2})
        uow = await fx_uow_factory()
        await uow.page_step_states.save(first)
        await uow.page_step_states.save(second)
        await uow.commit()
        states = (await fx_uow_factory()).page_step_states
        assert (await states.find(first.key), await states.find(second.key)) == (first, second)

    async def test_list_for_step_reads_one_step_over_several_pages(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the states of one step on the asked pages are listed, and those of other steps or pages are not.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, first_page = await _store_page(fx_uow_factory, fx_new_owner)
        _, second_page = await _store_page(fx_uow_factory, fx_new_owner)
        on_first = make_page_step_state(page_id=first_page, params={'max_angle_deg': 1})
        on_second = make_page_step_state(page_id=second_page, params={'max_angle_deg': 2})
        other_step = make_page_step_state(page_id=first_page, step_id=StepId(uuid4()), params={'max_angle_deg': 3})
        uow = await fx_uow_factory()
        for state in (on_first, on_second, other_step):
            await uow.page_step_states.save(state)
        await uow.commit()
        states = (await fx_uow_factory()).page_step_states
        listed = await states.list_for_step([first_page], Stage.GEOMETRY, DESKEW_STEP_ID)
        both = await states.list_for_step([first_page, second_page], Stage.GEOMETRY, DESKEW_STEP_ID)
        wrong_stage = await states.list_for_step([first_page], Stage.CLEANUP, DESKEW_STEP_ID)
        assert (listed, sorted(both, key=lambda state: str(state.page_id)), wrong_stage) == (
            [on_first],
            sorted([on_first, on_second], key=lambda state: str(state.page_id)),
            [],
        )

    async def test_state_of_a_missing_page_is_not_found(self, fx_uow_factory: UnitOfWorkFactory) -> None:
        """Reject a state whose page is not stored.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        """
        uow = await fx_uow_factory()
        with pytest.raises(NotFoundError):
            await uow.page_step_states.save(make_page_step_state(page_id=PageId(new_account_id())))

    async def test_delete_removes_a_state(self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory) -> None:
        """Verify a deleted state is gone, and deleting it again is a NotFoundError.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = await fx_uow_factory()
        state = make_page_step_state(page_id=page_id, params={'max_angle_deg': 3})
        await uow.page_step_states.save(state)
        await uow.page_step_states.delete(state.key)
        with pytest.raises(NotFoundError):
            await uow.page_step_states.delete(state.key)


class TestPageStepChangeRepository:
    """Tests for the history of the layers of the steps of the pages."""

    async def test_change_survives_the_store(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a change reads back with its layers before and after, its source and its batch.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        change = evolve(
            make_page_step_change(page_id=page_id, before=None, after={'method': 'otsu'}),
            source=ChangeSource.CARRY_OVER,
            batch_id=ChangeBatchId(uuid4()),
            undoes=PageStepChangeId(uuid4()),
        )
        uow = await fx_uow_factory()
        stored = await uow.page_step_changes.add(change)
        await uow.commit()
        assert (await (await fx_uow_factory()).page_step_changes.get(change.id), stored) == (
            stored,
            evolve(change, sequence=1),
        )

    async def test_changes_made_at_the_same_instant_list_in_the_order_they_were_written(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the sequence, not the time or the identifier, orders the history, across transactions and a batch.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        written = [make_page_step_change(page_id=page_id, created_at=EPOCH) for _ in range(6)]
        uow = await fx_uow_factory()
        for change in written[:2]:
            await uow.page_step_changes.add(change)
        await uow.commit()
        uow = await fx_uow_factory()
        await uow.page_step_changes.add_many(written[2:5])
        await uow.page_step_changes.add(written[5])
        await uow.commit()
        listed = await (await fx_uow_factory()).page_step_changes.list_for_page(page_id)
        assert ([change.id for change in listed], [change.sequence for change in listed]) == (
            [change.id for change in written],
            [1, 2, 3, 4, 5, 6],
        )

    async def test_list_for_page_is_oldest_first_and_filters_by_stage(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the history of a page is listed from the oldest change, and one stage apart from the others.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        later = make_page_step_change(page_id=page_id, created_at=EPOCH + RECENT)
        earlier = make_page_step_change(page_id=page_id, created_at=EPOCH)
        cleanup = make_page_step_change(page_id=page_id, stage=Stage.CLEANUP, created_at=EPOCH + OLD)
        uow = await fx_uow_factory()
        await uow.page_step_changes.add_many([earlier, later, cleanup])
        listed = await uow.page_step_changes.list_for_page(page_id)
        in_stage = await uow.page_step_changes.list_for_page(page_id, Stage.GEOMETRY)
        assert ([change.id for change in listed], [change.id for change in in_stage]) == (
            [earlier.id, later.id, cleanup.id],
            [earlier.id, later.id],
        )

    async def test_list_for_batch_lists_the_changes_of_the_batch_of_every_page(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a batch lists its changes across pages, by page and sequence, and leaves out the other changes.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, first_page = await _store_page(fx_uow_factory, fx_new_owner)
        uow = await fx_uow_factory()
        second_page = make_page(project_id=project_id, order_key=SECOND_ORDER_KEY)
        await uow.pages.add(second_page)
        batch = ChangeBatchId(uuid4())
        members = [
            evolve(make_page_step_change(page_id=page_id), batch_id=batch)
            for page_id in (first_page, second_page.id, first_page)
        ]
        outside = [
            make_page_step_change(page_id=first_page),
            evolve(make_page_step_change(page_id=first_page), batch_id=ChangeBatchId(uuid4())),
        ]
        stored = await uow.page_step_changes.add_many([*members, *outside])
        await uow.commit()
        listed = await (await fx_uow_factory()).page_step_changes.list_for_batch(batch)
        expected = sorted(stored[: len(members)], key=attrgetter('page_id', 'sequence'))
        assert listed == expected

    async def test_list_undoing_finds_the_undos_of_the_given_changes_only(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the changes that take back the given changes are listed, and a change that stands has none.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        taken, standing, other = (make_page_step_change(page_id=page_id) for _ in range(3))
        undo = evolve(make_page_step_change(page_id=page_id), source=ChangeSource.UNDO, undoes=taken.id)
        unrelated = evolve(make_page_step_change(page_id=page_id), source=ChangeSource.UNDO, undoes=other.id)
        uow = await fx_uow_factory()
        await uow.page_step_changes.add_many([taken, standing, other, undo, unrelated])
        await uow.commit()
        found = await (await fx_uow_factory()).page_step_changes.list_undoing([taken.id, standing.id])
        nothing = await (await fx_uow_factory()).page_step_changes.list_undoing([])
        assert ([change.id for change in found], nothing) == ([undo.id], [])

    async def test_delete_for_step_deletes_that_step_on_that_page_only(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify every change of one step on one page goes, whatever its source, and the rest of the history stays.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = await fx_uow_factory()
        other_page = make_page(project_id=project_id, order_key=SECOND_ORDER_KEY)
        await uow.pages.add(other_page)
        doomed = [make_page_step_change(page_id=page_id) for _ in range(3)]
        undo = evolve(make_page_step_change(page_id=page_id), source=ChangeSource.UNDO, undoes=doomed[0].id)
        carried = evolve(
            make_page_step_change(page_id=page_id), source=ChangeSource.CARRY_OVER, batch_id=ChangeBatchId(uuid4())
        )
        other_step = evolve(make_page_step_change(page_id=page_id), step_id=StepId(uuid4()))
        other_stage = make_page_step_change(page_id=page_id, stage=Stage.CLEANUP)
        elsewhere = make_page_step_change(page_id=other_page.id)
        await uow.page_step_changes.add_many([*doomed, undo, carried, other_step, other_stage, elsewhere])
        await uow.commit()
        uow = await fx_uow_factory()
        deleted = await uow.page_step_changes.delete_for_step(PageStepKey(page_id, Stage.GEOMETRY, DESKEW_STEP_ID))
        await uow.commit()
        reading = await fx_uow_factory()
        assert (
            deleted,
            [change.id for change in await reading.page_step_changes.list_for_page(page_id)],
            [change.id for change in await reading.page_step_changes.list_for_page(other_page.id)],
        ) == (5, [other_step.id, other_stage.id], [elsewhere.id])

    async def test_delete_for_step_of_a_step_with_no_history_deletes_nothing(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a step with no change on the page is a count of zero and not an error.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = await fx_uow_factory()
        assert await uow.page_step_changes.delete_for_step(PageStepKey(page_id, Stage.GEOMETRY, DESKEW_STEP_ID)) == 0

    async def test_a_change_added_after_delete_for_step_is_numbered_above_every_remaining_one(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the sequence of the page goes on above the highest one it still holds, whichever changes were deleted.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        other_step = StepId(uuid4())
        uow = await fx_uow_factory()
        written = [
            make_page_step_change(page_id=page_id),
            evolve(make_page_step_change(page_id=page_id), step_id=other_step),
            make_page_step_change(page_id=page_id),
        ]
        await uow.page_step_changes.add_many(written)
        await uow.commit()
        uow = await fx_uow_factory()
        await uow.page_step_changes.delete_for_step(PageStepKey(page_id, Stage.GEOMETRY, DESKEW_STEP_ID))
        added = await uow.page_step_changes.add(make_page_step_change(page_id=page_id))
        await uow.commit()
        listed = await (await fx_uow_factory()).page_step_changes.list_for_page(page_id)
        assert ([change.sequence for change in listed], added.sequence) == ([2, 4], 4)

    async def test_change_of_a_missing_page_is_not_found(self, fx_uow_factory: UnitOfWorkFactory) -> None:
        """Reject a change whose page is not stored.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        """
        uow = await fx_uow_factory()
        with pytest.raises(NotFoundError):
            await uow.page_step_changes.add(make_page_step_change(page_id=PageId(new_account_id())))

    async def test_change_is_stored_once(self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory) -> None:
        """Verify a change with an identifier that is stored already is a conflict.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        change = make_page_step_change(page_id=page_id)
        uow = await fx_uow_factory()
        await uow.page_step_changes.add(change)
        await uow.commit()
        with pytest.raises(ConflictError):
            await (await fx_uow_factory()).page_step_changes.add(change)


class TestPageVersionProcessing:
    """Tests for the queries a run and a collection make on page versions, and for the columns processing added."""

    async def test_scale_edit_and_pyramid_survive_the_store(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the scale, the edit hash, the state of the pyramid and the review mark of a version read back.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = await fx_uow_factory()
        base = make_page_version(page_id=page_id)
        version = evolve(
            _version(base, scale=VersionScale.PREVIEW, edit_hash='0123456789abcdef', tiles_ready=True),
            review=ReviewReason.LOW_CONFIDENCE,
        )
        await uow.page_versions.add_many([base, version])
        await uow.commit()
        assert await (await fx_uow_factory()).page_versions.get(version.id) == version

    async def test_find_returns_a_version_by_its_identifier_or_none(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a repeated run finds the version of the earlier run, and a new one finds nothing.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = await fx_uow_factory()
        base = make_page_version(page_id=page_id)
        await uow.page_versions.add(base)
        assert (await uow.page_versions.find(base.id), await uow.page_versions.find(PageVersionId('f' * 16))) == (
            base,
            None,
        )

    async def test_list_by_ids_reads_the_versions_found_and_leaves_out_the_others(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify one read gives the stored versions among the identifiers, the earliest first.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = await fx_uow_factory()
        older = make_page_version(page_id=page_id, minutes=1)
        newer = make_page_version(page_id=page_id, minutes=2)
        unwanted = make_page_version(page_id=page_id, minutes=3)
        await uow.page_versions.add_many([newer, older, unwanted])
        found = await uow.page_versions.list_by_ids([newer.id, older.id, PageVersionId('f' * 16)])
        assert [version.id for version in found] == [older.id, newer.id]

    async def test_list_for_stage_filters_and_pages_the_versions_of_a_page(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the versions of a page are filtered by stage and scale and cut into windows with their total.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = await fx_uow_factory()
        base = make_page_version(page_id=page_id)
        first = evolve(_version(base), created_at=EPOCH + timedelta(minutes=1))
        second = evolve(_version(base), created_at=EPOCH + timedelta(minutes=2))
        preview = evolve(_version(base, scale=VersionScale.PREVIEW), created_at=EPOCH + timedelta(minutes=3))
        cleanup = evolve(_version(base, stage=Stage.CLEANUP), created_at=EPOCH + timedelta(minutes=4))
        await uow.page_versions.add_many([base, first, second, preview, cleanup])
        repository = uow.page_versions
        everything = SliceRequest(limit=10)
        full_geometry = await repository.list_for_stage(page_id, Stage.GEOMETRY, VersionScale.FULL, everything)
        previews = await repository.list_for_stage(page_id, None, VersionScale.PREVIEW, everything)
        window = await repository.list_for_stage(page_id, Stage.GEOMETRY, None, SliceRequest(offset=1, limit=2))
        assert (
            [version.id for version in full_geometry.items],
            [version.id for version in previews.items],
            ([version.id for version in window.items], window.total),
        ) == ([first.id, second.id], [preview.id], ([second.id, preview.id], 3))

    async def test_list_for_stage_filters_the_versions_by_mark(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a mark narrows the versions to those that carry it, and no mark lists marked and unmarked alike.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = await fx_uow_factory()
        base = make_page_version(page_id=page_id)
        bad = evolve(_version(base), mark=ResultMark.BAD, created_at=EPOCH + timedelta(minutes=1))
        good = evolve(_version(base), mark=ResultMark.GOOD, created_at=EPOCH + timedelta(minutes=2))
        unmarked = evolve(_version(base), created_at=EPOCH + timedelta(minutes=3))
        await uow.page_versions.add_many([base, bad, good, unmarked])
        repository = uow.page_versions
        everything = SliceRequest(limit=10)
        listed_bad = await repository.list_for_stage(page_id, Stage.GEOMETRY, None, everything, ResultMark.BAD)
        listed_good = await repository.list_for_stage(page_id, None, VersionScale.FULL, everything, ResultMark.GOOD)
        listed_all = await repository.list_for_stage(page_id, Stage.GEOMETRY, None, everything)
        assert (
            [version.id for version in listed_bad.items],
            [version.id for version in listed_good.items],
            [version.id for version in listed_all.items],
        ) == ([bad.id], [good.id], [bad.id, good.id, unmarked.id])

    async def test_collectable_is_old_non_base_and_outside_the_current_chains(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a collection may delete old versions that no page shows and no chain of a shown version needs.

        The base version stays, a head and the version it was computed from stay, a recent version stays, and an old
        full run and an old preview that nothing refers to may go.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = await fx_uow_factory()
        base = evolve(make_page_version(page_id=page_id), created_at=EPOCH)
        feeder = _version(base)
        head = evolve(_version(base, stage=Stage.CLEANUP), input_id=feeder.id)
        orphan = _version(base)
        recent = evolve(_version(base), created_at=NOW - RECENT)
        old_preview = _version(base, scale=VersionScale.PREVIEW)
        young_preview = evolve(_version(base, scale=VersionScale.PREVIEW), created_at=NOW - timedelta(minutes=5))
        await uow.page_versions.add_many([base, feeder, head, orphan, recent, old_preview, young_preview])
        await uow.page_stages.save(make_page_stage(page_id=page_id, stage=Stage.CLEANUP, head_version_id=head.id))
        found = await uow.page_versions.collectable(project_id, FULL_CUTOFF, PREVIEW_CUTOFF)
        assert {version.id for version in found} == {orphan.id, old_preview.id}

    async def test_collectable_keeps_the_inputs_of_every_version_that_stays(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify an old input goes only with the versions that read it, and not from under one that stays.

        A recent version that no page shows still reads its old input, which the database would leave it without if the
        input were deleted, and the old input of that input stays too. A chain of old versions that nothing outside it
        reads goes whole.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = await fx_uow_factory()
        base = evolve(make_page_version(page_id=page_id), created_at=EPOCH)
        first = _version(base)
        second = evolve(_version(base), input_id=first.id)
        reader = evolve(_version(base), input_id=second.id, created_at=NOW - RECENT)
        chain_start = _version(base)
        chain_end = evolve(_version(base), input_id=chain_start.id)
        await uow.page_versions.add_many([base, first, second, reader, chain_start, chain_end])
        found = await uow.page_versions.collectable(project_id, FULL_CUTOFF, PREVIEW_CUTOFF)
        assert {version.id for version in found} == {chain_start.id, chain_end.id}

    async def test_delete_many_removes_the_versions_and_ignores_missing_ones(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify several versions go in one call, and an identifier that is not stored is no error.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = await fx_uow_factory()
        base = make_page_version(page_id=page_id)
        doomed = [_version(base), _version(base)]
        await uow.page_versions.add_many([base, *doomed])
        await uow.commit()
        uow = await fx_uow_factory()
        await uow.page_versions.delete_many([*(version.id for version in doomed), PageVersionId('f' * 16)])
        await uow.commit()
        assert [version.id for version in await (await fx_uow_factory()).page_versions.list_for_page(page_id)] == [
            base.id
        ]


class TestJobParams:
    """Tests for the parameters of a processing job."""

    async def test_params_survive_the_store(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the parameters of a job read back, and a job without any reads back empty.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, _ = await _store_page(fx_uow_factory, fx_new_owner)
        uow = await fx_uow_factory()
        with_params = evolve(make_job(project_id=project_id), params={'stage': 'geometry', 'page_ids': []})
        await uow.jobs.add(with_params)
        await uow.commit()
        assert (await (await fx_uow_factory()).jobs.get(with_params.id)).params == with_params.params
