"""Contract of the persistence ports of processing: recipes, stage records, the state and history of steps and page versions.

Every test runs against each adapter registered in the conftest, the in-memory one and the SQL one, so both keep the
promises of the ports: the keys they check, the actions of the foreign keys, and the queries a run and a collection
make.
"""

from datetime import timedelta
from functools import partial
from operator import attrgetter
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.entities import PageEdit
from bookreviver.domain.enums import (
    ChangeSource,
    PageOrigin,
    RecipeKind,
    ResultMark,
    ReviewReason,
    Stage,
    StageState,
    ValueScope,
    VersionScale,
    VersionState,
)
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.geometry import Line, Point
from bookreviver.domain.ids import ChangeBatchId, PageId, PageStepChangeId, PageVersionId, ProjectId, StepId
from bookreviver.domain.stage_summaries import StepTally
from bookreviver.domain.values import PageStageKey, PageStepKey, ProcessorRef, SliceRequest, Step
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
    make_step_values,
    new_account_id,
)
from tests.helpers.seeding import store_project

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.entities import Page, PageStage, PageStepChange, PageVersion, Project
    from bookreviver.domain.step_values import StepValues
    from bookreviver.ports.persistence import Repositories, UnitOfWork
    from tests.contracts.conftest import OwnerFactory, UnitOfWorkFactory

pytestmark = pytest.mark.anyio

# A version created long before the moment a collection looks back to, and one created after it
OLD: timedelta = timedelta(days=60)
RECENT: timedelta = timedelta(days=1)
# Order key of a second page of the same book, after the 'a0' a stored page gets
SECOND_ORDER_KEY: str = 'a1'
NOW = EPOCH + timedelta(days=100)
PREVIEW_CUTOFF = NOW - timedelta(hours=1)
# The parameter of the deskew step that the tests of the settings of a step give values
MAX_ANGLE: str = 'max_angle_deg'
# The identifier of a version that no test stores
MISSING_VERSION_ID: PageVersionId = PageVersionId('f' * 16)
# The labels of two groups of pages
INDEX_GROUP: str = 'Index'
APPENDIX_GROUP: str = 'Appendix'
# The method parameter of a step and one of its values
METHOD: str = 'method'
PROJECTION: str = 'projection'
# The widest window of versions or states a test reads
EVERYTHING: SliceRequest = SliceRequest(limit=10)
# The attribute the records of the pages are sorted by, and the key of a processor that blanks pages
PAGE_ID: str = 'page_id'
BLANK_PROCESSOR: str = 'pages.blank'


def _save_records(repos: Repositories, _project: Project, *, records: Sequence[PageStage]) -> None:
    """Save stage records one after another, as a run that advances several stages does.

    :param repos: Repositories of the transaction.
    :type repos: Repositories
    :param _project: Project of the book, which the work does not need.
    :type _project: Project
    :param records: Records to save.
    :type records: Sequence[PageStage]
    """
    for record in records:
        repos.page_stages.save(record)


def _save_values(repos: Repositories, _project: Project, *, values: Sequence[StepValues]) -> None:
    """Save the values of steps one after another.

    :param repos: Repositories of the transaction.
    :type repos: Repositories
    :param _project: Project of the book, which the work does not need.
    :type _project: Project
    :param values: Values to save.
    :type values: Sequence[StepValues]
    """
    for part in values:
        repos.step_values.save(part)


def _add_head(repos: Repositories, _project: Project, *, version: PageVersion, record: PageStage) -> None:
    """Add a version and the stage record that names it as the current version.

    :param repos: Repositories of the transaction.
    :type repos: Repositories
    :param _project: Project of the book, which the work does not need.
    :type _project: Project
    :param version: Version to add.
    :type version: PageVersion
    :param record: Record naming the version.
    :type record: PageStage
    """
    repos.page_versions.add(version)
    repos.page_stages.save(record)


async def _store_page(uow_factory: UnitOfWorkFactory, new_owner: OwnerFactory) -> tuple[ProjectId, PageId]:
    """Store a project with one page, each in the change that owns it.

    :param uow_factory: Function opening a new unit of work of the backend under test.
    :type uow_factory: UnitOfWorkFactory
    :param new_owner: Function creating an account the backend accepts as an owner.
    :type new_owner: OwnerFactory
    :returns: The identifiers of the project and its page.
    :rtype: tuple[ProjectId, PageId]
    """
    project = make_project(owner_id=await new_owner())
    page = make_page(project_id=project.id)
    await store_project(uow_factory(), project, page)
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

    :param uow: Unit of work to add to, in which the project of the page is stored.
    :type uow: UnitOfWork
    :param page: The page, which is stored.
    :type page: Page
    :returns: The identifier of the marked version.
    :rtype: PageVersionId
    """
    base = make_page_version(page_id=page.id)
    head = evolve(_version(base), review=ReviewReason.LOW_CONFIDENCE)
    await uow.change_book(page.project_id, lambda repos, _project: repos.page_versions.add_many([base, head]))
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

    async def test_stage_has_one_recipe_for_each_kind(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Reject a second recipe of a kind in a stage, which two requests making the recipes could both store.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        uow = fx_uow_factory()
        project = make_project(owner_id=await fx_new_owner())
        await store_project(uow, project)
        first = make_recipe(project_id=project.id, kind=RecipeKind.BLANK)
        second = make_recipe(project_id=project.id, kind=RecipeKind.BLANK, minutes=1)
        await uow.change_book(project.id, lambda repos, _project: repos.recipes.add(first))
        with pytest.raises(ConflictError):
            await uow.change_book(project.id, lambda repos, _project: repos.recipes.add(second))

    async def test_every_kind_and_other_stages_may_be_added_beside_a_recipe(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the one recipe of a kind is per stage, and the recipes of the stage are listed by creation.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        uow = fx_uow_factory()
        project = make_project(owner_id=await fx_new_owner())
        await store_project(uow, project)
        recipes = [
            make_recipe(project_id=project.id, kind=kind, minutes=index) for index, kind in enumerate(RecipeKind)
        ]
        other_stage = make_recipe(project_id=project.id, stage=Stage.CLEANUP)
        await uow.change_book(
            project.id, lambda repos, _project: repos.recipes.add_many([*reversed(recipes), other_stage])
        )
        listed = await fx_uow_factory().read(lambda repos: repos.recipes.list_for_stage(project.id, Stage.GEOMETRY))
        assert [recipe.id for recipe in listed] == [recipe.id for recipe in recipes]

    async def test_list_for_project_returns_the_recipes_of_each_stage_in_pipeline_order(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the recipes of another project are left out, and the stages keep the pipeline order.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        project, other = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        cleanup = make_recipe(project_id=project.id, stage=Stage.CLEANUP)
        geometry = make_recipe(project_id=project.id)
        picture = make_recipe(project_id=project.id, kind=RecipeKind.BW_PICTURE, minutes=1)
        elsewhere = make_recipe(project_id=other.id)
        uow = fx_uow_factory()
        for owned in (project, other):
            await store_project(uow, owned)
        await uow.change_book(project.id, lambda repos, _project: repos.recipes.add_many([cleanup, picture, geometry]))
        await uow.change_book(other.id, lambda repos, _project: repos.recipes.add(elsewhere))
        listed = await fx_uow_factory().read(lambda repos: repos.recipes.list_for_project(project.id))
        assert [recipe.id for recipe in listed] == [geometry.id, picture.id, cleanup.id]

    async def test_steps_survive_the_store(self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory) -> None:
        """Verify a recipe reads back with its steps, their parameters, identifiers and switches.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        uow = fx_uow_factory()
        project = make_project(owner_id=await fx_new_owner())
        plain = make_recipe(project_id=project.id)
        other = Step(processor_key='geometry.other', params={}, enabled=False)
        recipe = evolve(plain, steps=(*plain.steps, other, evolve(plain.steps[0], step_id=StepId(uuid4()))))
        await store_project(uow, project)
        await uow.change_book(project.id, lambda repos, _project: repos.recipes.add(recipe))
        assert await fx_uow_factory().read(lambda repos: repos.recipes.get(recipe.id)) == recipe

    async def test_recipe_of_a_missing_project_is_not_found(self, fx_uow_factory: UnitOfWorkFactory) -> None:
        """Reject a recipe whose project is not stored.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        """
        uow = fx_uow_factory()
        recipe = make_recipe(project_id=ProjectId(new_account_id()))
        with pytest.raises(NotFoundError):
            await uow.change(lambda repos: repos.recipes.add(recipe))

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
        recipe = make_recipe(project_id=project_id)

        def add_recipe_and_record(repos: Repositories, _project: Project) -> None:
            """Add the recipe and a stage record that names it.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            repos.recipes.add(recipe)
            repos.page_stages.save(make_page_stage(page_id=page_id, recipe_id=recipe.id))

        await fx_uow_factory().change_book(project_id, add_recipe_and_record)
        await fx_uow_factory().change_book(project_id, lambda repos, _project: repos.recipes.delete(recipe.id))
        kept = await fx_uow_factory().read(lambda repos: repos.page_stages.get(PageStageKey(page_id, Stage.GEOMETRY)))
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
        recipe = make_recipe(project_id=project_id)
        await fx_uow_factory().change_book(project_id, lambda repos, _project: repos.recipes.add(recipe))
        await fx_uow_factory().change(lambda repos: repos.projects.delete(project_id))
        with pytest.raises(NotFoundError):
            await fx_uow_factory().read(lambda repos: repos.recipes.get(recipe.id))


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
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        stale = make_page_stage(page_id=page_id, state=StageState.STALE)

        def save_twice(repos: Repositories, _project: Project) -> None:
            """Save a record of the stage and then a stale one in its place.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            repos.page_stages.save(make_page_stage(page_id=page_id))
            repos.page_stages.save(stale)

        await fx_uow_factory().change_book(project_id, save_twice)
        found = await fx_uow_factory().read(lambda repos: repos.page_stages.find(PageStageKey(page_id, Stage.GEOMETRY)))
        assert found == stale

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
        uow = fx_uow_factory()
        key = PageStageKey(page_id, Stage.CLEANUP)
        assert await uow.read(lambda repos: repos.page_stages.find(key)) is None
        with pytest.raises(NotFoundError):
            await uow.read(lambda repos: repos.page_stages.get(key))

    async def test_record_of_a_missing_page_is_not_found(self, fx_uow_factory: UnitOfWorkFactory) -> None:
        """Reject a record whose page is not stored.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        """
        uow = fx_uow_factory()
        record = make_page_stage(page_id=PageId(new_account_id()))
        with pytest.raises(NotFoundError):
            await uow.change(lambda repos: repos.page_stages.save(record))

    async def test_list_for_page_follows_the_order_of_the_stages(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the records of a page come in pipeline order, whatever order they were saved in.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = fx_uow_factory()

        def save_out_of_order(repos: Repositories, _project: Project) -> None:
            """Save a record for each of three stages, the last stage first.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            for stage in (Stage.CLEANUP, Stage.PAGE_SPLIT, Stage.GEOMETRY):
                repos.page_stages.save(make_page_stage(page_id=page_id, stage=stage))

        await uow.change_book(project_id, save_out_of_order)
        listed = await uow.read(lambda repos: repos.page_stages.list_for_page(page_id))
        assert [record.stage for record in listed] == [Stage.PAGE_SPLIT, Stage.GEOMETRY, Stage.CLEANUP]

    async def test_list_for_pages_reads_the_records_of_the_given_pages_only(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify one read gives the records of several pages in pipeline order, and none of the other pages.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        first_book, second_book, other_book = [await _store_page(fx_uow_factory, fx_new_owner) for _ in range(3)]
        first, second, other = first_book[1], second_book[1], other_book[1]
        uow = fx_uow_factory()
        for project_id, page_id in (first_book, second_book, other_book):
            records = [make_page_stage(page_id=page_id, stage=stage) for stage in (Stage.GEOMETRY, Stage.PAGE_SPLIT)]
            await uow.change_book(project_id, partial(_save_records, records=records))
        found = await uow.read(lambda repos: repos.page_stages.list_for_pages([first, second]))
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
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = fx_uow_factory()
        record = make_page_stage(page_id=page_id, head_version_id=PageVersionId('0' * 16))
        with pytest.raises(NotFoundError):
            await uow.change_book(project_id, lambda repos, _project: repos.page_stages.save(record))

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
        uow = fx_uow_factory()
        recipe = make_recipe(project_id=project_id)
        mine = make_page_stage(page_id=page_id, recipe_id=recipe.id)
        theirs = make_page_stage(page_id=other_page_id)

        def add_recipe_and_records(repos: Repositories, _project: Project) -> None:
            """Add the recipe, the record that names it and a record of another stage.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            repos.recipes.add(recipe)
            repos.page_stages.save(mine)
            repos.page_stages.save(make_page_stage(page_id=page_id, stage=Stage.CLEANUP))

        await uow.change_book(project_id, add_recipe_and_records)
        await uow.change_book(other_project_id, lambda repos, _project: repos.page_stages.save(theirs))
        found = await fx_uow_factory().read(
            lambda repos: (
                repos.page_stages.list_for_recipe(recipe.id),
                repos.page_stages.list_for_project_stage(project_id, Stage.GEOMETRY),
                repos.page_stages.list_for_project_stage(other_project_id, Stage.CLEANUP),
            )
        )
        assert found == ([mine], [mine], [])

    async def test_list_fresh_returns_the_fresh_records_of_every_project(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the fresh records of all the projects are listed by page and stage, and no stale or failed one.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        other_project_id, other_page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = fx_uow_factory()
        fresh = [
            make_page_stage(page_id=page_id, stage=Stage.CLEANUP),
            make_page_stage(page_id=page_id, stage=Stage.GEOMETRY),
            make_page_stage(page_id=other_page_id, stage=Stage.GEOMETRY),
        ]
        stale = make_page_stage(page_id=page_id, stage=Stage.PAGE_SPLIT, state=StageState.STALE)
        failed = make_page_stage(page_id=other_page_id, stage=Stage.CLEANUP, state=StageState.FAILED)
        await uow.change_book(project_id, partial(_save_records, records=[*fresh[:2], stale]))
        await uow.change_book(other_project_id, partial(_save_records, records=[fresh[2], failed]))
        order = list(Stage)
        expected = sorted(fresh, key=lambda record: (str(record.page_id), order.index(record.stage)))

        assert await fx_uow_factory().read(lambda repos: repos.page_stages.list_fresh()) == expected

    async def test_list_replaced_returns_the_records_made_by_another_version_of_the_processor_in_any_state(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a stage's records of every project are listed when their version is of the key but not the given one.

        A record of the installed version, one of another processor and one of another stage are left out, and the
        state of the record does not matter.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        installed = ProcessorRef(key=BLANK_PROCESSOR, version='2')
        books = [await _store_page(fx_uow_factory, fx_new_owner) for _ in range(4)]
        pages = [page_id for _, page_id in books]
        made_by = [
            ProcessorRef(key=BLANK_PROCESSOR, version='1'),
            installed,
            ProcessorRef(key='geometry.crop', version='1'),
            ProcessorRef(key=BLANK_PROCESSOR, version='1'),
        ]
        uow = fx_uow_factory()
        records = []
        for (project_id, page_id), processor in zip(books, made_by, strict=True):
            version = evolve(make_page_version(page_id=page_id), stage=Stage.PAGE_ORDER, processor=processor)
            state = StageState.STALE if page_id == pages[0] else StageState.FRESH
            record = make_page_stage(page_id=page_id, stage=Stage.PAGE_ORDER, head_version_id=version.id, state=state)
            records.append(record)
            await uow.change_book(project_id, partial(_add_head, version=version, record=record))
        other_stage = evolve(make_page_version(page_id=pages[3]), stage=Stage.GEOMETRY, processor=made_by[0])
        other_record = make_page_stage(page_id=pages[3], stage=Stage.GEOMETRY, head_version_id=other_stage.id)
        await uow.change_book(books[3][0], partial(_add_head, version=other_stage, record=other_record))

        found = await fx_uow_factory().read(lambda repos: repos.page_stages.list_replaced(Stage.PAGE_ORDER, installed))

        assert found == sorted([records[0], records[3]], key=attrgetter(PAGE_ID))

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
        uow = fx_uow_factory()
        base = make_page_version(page_id=page_id)
        version = _version(base)

        def add_versions_and_records(repos: Repositories, _project: Project) -> None:
            """Add the versions and two records, one of which names the version as its head.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            repos.page_versions.add_many([base, version])
            repos.page_stages.save(make_page_stage(page_id=page_id, head_version_id=version.id))
            repos.page_stages.save(make_page_stage(page_id=page_id, stage=Stage.CLEANUP))

        await uow.change_book(project_id, add_versions_and_records)
        assert set(await uow.read(lambda repos: repos.page_stages.head_ids(project_id))) == {version.id}

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
        uow = fx_uow_factory()
        marked, stale, failed = (_blank_page(project.id, f'a{number}') for number in range(3))
        placeholder = make_page(project_id=project.id, order_key='b0')
        elsewhere = _blank_page(other.id, 'a0')
        await store_project(uow, project, marked, stale, failed, placeholder)
        await store_project(uow, other, elsewhere)
        marked_head = await _add_marked_head(uow, marked)
        failed_head = await _add_marked_head(uow, failed)
        records = [
            make_page_stage(page_id=marked.id, head_version_id=marked_head),
            make_page_stage(page_id=stale.id, state=StageState.STALE),
            make_page_stage(page_id=failed.id, head_version_id=failed_head, state=StageState.FAILED),
            make_page_stage(page_id=placeholder.id),
            make_page_stage(page_id=marked.id, stage=Stage.CLEANUP),
        ]
        await uow.change_book(project.id, partial(_save_records, records=records))
        await uow.change_book(
            other.id, lambda repos, _project: repos.page_stages.save(make_page_stage(page_id=elsewhere.id))
        )
        tallies, nothing = await fx_uow_factory().read(
            lambda repos: (repos.page_stages.tally({project.id}), repos.page_stages.tally(set()))
        )
        counts = {(tally.stage, tally.fresh, tally.stale, tally.failed, tally.review, tally.check) for tally in tallies}
        assert counts == {(Stage.GEOMETRY, 1, 1, 1, 1, 3), (Stage.CLEANUP, 1, 0, 0, 0, 0)}
        assert {tally.project_id for tally in tallies} == {project.id}
        assert nothing == []

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
        uow = fx_uow_factory()
        both, clean = (_blank_page(project.id, f'a{number}') for number in range(2))
        await store_project(uow, project, both, clean)
        head_id = await _add_marked_head(uow, both)
        records = [
            make_page_stage(page_id=both.id, head_version_id=head_id, state=StageState.STALE),
            make_page_stage(page_id=clean.id),
        ]
        await uow.change_book(project.id, partial(_save_records, records=records))
        [tally] = await fx_uow_factory().read(lambda repos: repos.page_stages.tally({project.id}))
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
        uow = fx_uow_factory()
        first, second, whole, failed = (_blank_page(project.id, f'a{number}') for number in range(4))
        placeholder = make_page(project_id=project.id, order_key='b1')
        elsewhere = _blank_page(other.id, 'a1')
        await store_project(uow, project, first, second, whole, failed, placeholder)
        await store_project(uow, other, elsewhere)
        stopped = [
            evolve(make_page_stage(page_id=page.id, state=state), through_step=through_step)
            for page, through_step, state in (
                (first, 0, StageState.FRESH),
                (second, 0, StageState.STALE),
                (whole, None, StageState.FRESH),
                (failed, 1, StageState.FAILED),
                (placeholder, 0, StageState.FRESH),
            )
        ]
        elsewhere_stopped = evolve(make_page_stage(page_id=elsewhere.id), through_step=0)
        await uow.change_book(project.id, partial(_save_records, records=stopped))
        await uow.change_book(other.id, lambda repos, _project: repos.page_stages.save(elsewhere_stopped))
        [tally], steps = await fx_uow_factory().read(
            lambda repos: (repos.page_stages.tally({project.id}), repos.page_stages.step_tally(project.id))
        )
        assert steps == [StepTally(stage=Stage.GEOMETRY, through_step=0, pages=2)]
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
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        base = make_page_version(page_id=page_id)
        version = _version(base)

        def add_versions_and_record(repos: Repositories, _project: Project) -> None:
            """Add the versions and a record that names the version as its head.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            repos.page_versions.add_many([base, version])
            repos.page_stages.save(make_page_stage(page_id=page_id, head_version_id=version.id))

        await fx_uow_factory().change_book(project_id, add_versions_and_record)
        await fx_uow_factory().change_book(project_id, lambda repos, _project: repos.page_versions.delete(version.id))
        kept = await fx_uow_factory().read(lambda repos: repos.page_stages.get(PageStageKey(page_id, Stage.GEOMETRY)))
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
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)

        def add_records(repos: Repositories, _project: Project) -> None:
            """Add a stage record, a step state with an edit and a change of the history of a step.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            repos.page_stages.save(make_page_stage(page_id=page_id))
            repos.page_step_states.save(make_page_step_state(page_id=page_id, edit=make_page_edit(page_id=page_id)))
            repos.page_step_changes.add(make_page_step_change(page_id=page_id))

        await fx_uow_factory().change_book(project_id, add_records)
        await fx_uow_factory().change_book(project_id, lambda repos, _project: repos.pages.delete(page_id))
        remaining = await fx_uow_factory().read(
            lambda repos: (
                repos.page_stages.find(PageStageKey(page_id, Stage.GEOMETRY)),
                repos.page_step_states.list_for_page(page_id),
                repos.page_step_changes.list_for_page(page_id),
            )
        )
        assert remaining == (None, [], [])


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
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        first = make_page_step_state(page_id=page_id, params={MAX_ANGLE: 3})
        replacement = make_page_step_state(page_id=page_id, params={MAX_ANGLE: 7})

        def save_twice(repos: Repositories, _project: Project) -> None:
            """Save a state and then another one of the same step in its place.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            repos.page_step_states.save(first)
            repos.page_step_states.save(replacement)

        await fx_uow_factory().change_book(project_id, save_twice)
        key = PageStepKey(page_id, Stage.GEOMETRY, DESKEW_STEP_ID)
        assert await fx_uow_factory().read(lambda repos: repos.page_step_states.find(key)) == replacement

    async def test_settings_and_edit_survive_the_store_together(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the fields a page changes and its manual edit read back, and a state may hold either alone.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        both = make_page_step_state(
            page_id=page_id, params={MAX_ANGLE: 3, METHOD: PROJECTION}, edit=make_page_edit(page_id=page_id)
        )
        settings_only = make_page_step_state(page_id=page_id, stage=Stage.CLEANUP, params={'threshold': 0.5})
        edit_only = make_page_step_state(
            page_id=page_id, stage=Stage.LAYOUT, edit=make_page_edit(page_id=page_id, stage=Stage.LAYOUT)
        )
        states = (both, settings_only, edit_only)

        def save_all_states(repos: Repositories, _project: Project) -> None:
            """Save the three states.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            for state in states:
                repos.page_step_states.save(state)

        await fx_uow_factory().change_book(project_id, save_all_states)
        read_back = await fx_uow_factory().read(
            lambda repos: [repos.page_step_states.get(state.key) for state in states]
        )
        assert read_back == [both, settings_only, edit_only]

    async def test_geometry_and_mask_survive_the_store(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the split line a user drew reads back as the same line in the edit of a state.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
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
        await fx_uow_factory().change_book(project_id, lambda repos, _project: repos.page_step_states.save(state))
        assert await fx_uow_factory().read(lambda repos: repos.page_step_states.get(state.key)) == state

    async def test_list_for_page_filters_by_stage(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the states of one stage are listed apart from those of the page's other stages.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = fx_uow_factory()
        geometry = make_page_step_state(page_id=page_id, params={MAX_ANGLE: 3})
        cleanup = evolve(geometry, stage=Stage.CLEANUP)

        def save_both(repos: Repositories, _project: Project) -> None:
            """Save the state of the later stage first.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            repos.page_step_states.save(cleanup)
            repos.page_step_states.save(geometry)

        await uow.change_book(project_id, save_both)
        listed = await uow.read(
            lambda repos: (
                repos.page_step_states.list_for_page(page_id, Stage.GEOMETRY),
                repos.page_step_states.list_for_page(page_id),
            )
        )
        assert listed == ([geometry], [geometry, cleanup])

    async def test_two_steps_of_one_processor_keep_two_states(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify states of two steps on one page and stage are stored apart and found each by its step.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        first = make_page_step_state(page_id=page_id, params={MAX_ANGLE: 1})
        second = make_page_step_state(page_id=page_id, step_id=StepId(uuid4()), params={MAX_ANGLE: 2})

        def save_both(repos: Repositories, _project: Project) -> None:
            """Save the states of the two steps.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            repos.page_step_states.save(first)
            repos.page_step_states.save(second)

        await fx_uow_factory().change_book(project_id, save_both)
        found = await fx_uow_factory().read(
            lambda repos: (repos.page_step_states.find(first.key), repos.page_step_states.find(second.key))
        )
        assert found == (first, second)

    async def test_list_for_step_reads_one_step_over_several_pages(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the states of one step on the asked pages are listed, and those of other steps or pages are not.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        first_project, first_page = await _store_page(fx_uow_factory, fx_new_owner)
        second_project, second_page = await _store_page(fx_uow_factory, fx_new_owner)
        on_first = make_page_step_state(page_id=first_page, params={MAX_ANGLE: 1})
        on_second = make_page_step_state(page_id=second_page, params={MAX_ANGLE: 2})
        other_step = make_page_step_state(page_id=first_page, step_id=StepId(uuid4()), params={MAX_ANGLE: 3})
        uow = fx_uow_factory()

        def save_first_book(repos: Repositories, _project: Project) -> None:
            """Save the two states of the first book.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            repos.page_step_states.save(on_first)
            repos.page_step_states.save(other_step)

        await uow.change_book(first_project, save_first_book)
        await uow.change_book(second_project, lambda repos, _project: repos.page_step_states.save(on_second))
        listed, both, wrong_stage = await fx_uow_factory().read(
            lambda repos: (
                repos.page_step_states.list_for_step([first_page], Stage.GEOMETRY, DESKEW_STEP_ID),
                repos.page_step_states.list_for_step([first_page, second_page], Stage.GEOMETRY, DESKEW_STEP_ID),
                repos.page_step_states.list_for_step([first_page], Stage.CLEANUP, DESKEW_STEP_ID),
            )
        )
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
        uow = fx_uow_factory()
        state = make_page_step_state(page_id=PageId(new_account_id()))
        with pytest.raises(NotFoundError):
            await uow.change(lambda repos: repos.page_step_states.save(state))

    async def test_delete_removes_a_state(self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory) -> None:
        """Verify a deleted state is gone, and deleting it again is a NotFoundError.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = fx_uow_factory()
        state = make_page_step_state(page_id=page_id, params={MAX_ANGLE: 3})

        def save_and_delete(repos: Repositories, _project: Project) -> None:
            """Save the state and delete it.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            repos.page_step_states.save(state)
            repos.page_step_states.delete(state.key)

        await uow.change_book(project_id, save_and_delete)
        with pytest.raises(NotFoundError):
            await uow.change_book(project_id, lambda repos, _project: repos.page_step_states.delete(state.key))


class TestStepValuesRepository:
    """Tests for the values of the steps for the odd pages, the even pages and the groups."""

    async def test_save_stores_values_and_replaces_them(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a second save for the same step and part of the pages replaces the values, and they read back.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, _ = await _store_page(fx_uow_factory, fx_new_owner)
        first = make_step_values(project_id=project_id, params={MAX_ANGLE: 3})
        replacement = make_step_values(project_id=project_id, params={MAX_ANGLE: 7, METHOD: PROJECTION})

        def save_twice(repos: Repositories, _project: Project) -> None:
            """Save values and then other values for the same step and part of the pages.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            repos.step_values.save(first)
            repos.step_values.save(replacement)

        await fx_uow_factory().change_book(project_id, save_twice)
        found = await fx_uow_factory().read(lambda repos: repos.step_values.find(replacement.key))
        assert found == replacement

    async def test_every_part_of_the_pages_keeps_its_own_values_of_a_step(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the odd pages, the even pages and two groups are four rows, listed by scope and group label.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, _ = await _store_page(fx_uow_factory, fx_new_owner)
        parts = [
            make_step_values(project_id=project_id, scope=ValueScope.GROUP, group_label=INDEX_GROUP),
            make_step_values(project_id=project_id, scope=ValueScope.EVEN, params={MAX_ANGLE: 4}),
            make_step_values(project_id=project_id, scope=ValueScope.ODD, params={MAX_ANGLE: 5}),
            make_step_values(project_id=project_id, scope=ValueScope.GROUP, group_label=APPENDIX_GROUP),
        ]
        await fx_uow_factory().change_book(project_id, partial(_save_values, values=parts))
        listed = await fx_uow_factory().read(lambda repos: repos.step_values.list_for_step(project_id, DESKEW_STEP_ID))
        assert [(values.scope, values.group_label) for values in listed] == [
            (ValueScope.GROUP, APPENDIX_GROUP),
            (ValueScope.GROUP, INDEX_GROUP),
            (ValueScope.ODD, ''),
            (ValueScope.EVEN, ''),
        ]

    async def test_lists_keep_the_steps_and_the_projects_apart(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the values of another step and another book are not listed, and a project lists every stage.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, _ = await _store_page(fx_uow_factory, fx_new_owner)
        other_project_id, _ = await _store_page(fx_uow_factory, fx_new_owner)
        mine = make_step_values(project_id=project_id)
        other_step = make_step_values(project_id=project_id, step_id=StepId(uuid4()))
        other_stage = evolve(make_step_values(project_id=project_id, step_id=StepId(uuid4())), stage=Stage.CLEANUP)
        other_book = make_step_values(project_id=other_project_id, step_id=StepId(uuid4()))
        uow = fx_uow_factory()
        await uow.change_book(project_id, partial(_save_values, values=[mine, other_step, other_stage]))
        await uow.change_book(other_project_id, lambda repos, _project: repos.step_values.save(other_book))
        listed = await fx_uow_factory().read(
            lambda repos: (
                repos.step_values.list_for_step(project_id, DESKEW_STEP_ID),
                repos.step_values.list_for_project(project_id),
            )
        )
        assert listed == (
            [mine],
            sorted([mine, other_step, other_stage], key=lambda values: str(values.step_id)),
        )

    async def test_two_projects_sharing_a_step_keep_separate_values(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify books built from one profile, which share a step id, keep, replace and delete their values apart.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        first_id, _ = await _store_page(fx_uow_factory, fx_new_owner)
        second_id, _ = await _store_page(fx_uow_factory, fx_new_owner)
        first = make_step_values(project_id=first_id, params={MAX_ANGLE: 3})
        second = make_step_values(project_id=second_id, params={MAX_ANGLE: 9})
        uow = fx_uow_factory()
        await uow.change_book(first_id, lambda repos, _project: repos.step_values.save(first))
        await uow.change_book(second_id, lambda repos, _project: repos.step_values.save(second))
        before = await fx_uow_factory().read(
            lambda repos: (
                repos.step_values.list_for_step(first_id, DESKEW_STEP_ID),
                repos.step_values.list_for_step(second_id, DESKEW_STEP_ID),
                repos.step_values.find(first.key),
                repos.step_values.find(second.key),
            )
        )
        expect(before == ([first], [second], first, second))
        await fx_uow_factory().change_book(first_id, lambda repos, _project: repos.step_values.delete(first.key))
        after = await fx_uow_factory().read(
            lambda repos: (repos.step_values.find(first.key), repos.step_values.find(second.key))
        )
        expect(after == (None, second))
        assert_expectations()

    async def test_delete_removes_one_part_and_a_missing_one_is_not_found(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a deleted part is gone, the other part stays, and deleting what is not stored is refused.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, _ = await _store_page(fx_uow_factory, fx_new_owner)
        even = make_step_values(project_id=project_id)
        odd = make_step_values(project_id=project_id, scope=ValueScope.ODD)
        uow = fx_uow_factory()
        await uow.change_book(project_id, partial(_save_values, values=[even, odd]))
        await fx_uow_factory().change_book(project_id, lambda repos, _project: repos.step_values.delete(even.key))
        with pytest.raises(NotFoundError):
            await uow.change_book(project_id, lambda repos, _project: repos.step_values.delete(even.key))
        found = await fx_uow_factory().read(
            lambda repos: (repos.step_values.find(even.key), repos.step_values.find(odd.key))
        )
        assert found == (None, odd)

    async def test_values_of_a_project_that_is_not_stored_are_refused(self, fx_uow_factory: UnitOfWorkFactory) -> None:
        """Verify values need their project, which is what removes them with it.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        """
        uow = fx_uow_factory()
        values = make_step_values(project_id=ProjectId(uuid4()))
        with pytest.raises(NotFoundError):
            await uow.change(lambda repos: repos.step_values.save(values))

    async def test_deleting_the_project_deletes_its_values(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the values of a project are removed with it and those of another project stay.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, _ = await _store_page(fx_uow_factory, fx_new_owner)
        other_project_id, _ = await _store_page(fx_uow_factory, fx_new_owner)
        uow = fx_uow_factory()
        kept = make_step_values(project_id=other_project_id, scope=ValueScope.ODD)
        mine = make_step_values(project_id=project_id)
        await uow.change_book(project_id, lambda repos, _project: repos.step_values.save(mine))
        await uow.change_book(other_project_id, lambda repos, _project: repos.step_values.save(kept))
        await fx_uow_factory().change(lambda repos: repos.projects.delete(project_id))
        remaining = await fx_uow_factory().read(
            lambda repos: repos.step_values.list_for_step(other_project_id, DESKEW_STEP_ID)
        )
        assert remaining == [kept]


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
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        change = evolve(
            make_page_step_change(page_id=page_id, before=None, after={METHOD: 'otsu'}),
            scope=ValueScope.GROUP,
            group_label=INDEX_GROUP,
            source=ChangeSource.CARRY_OVER,
            batch_id=ChangeBatchId(uuid4()),
            undoes=PageStepChangeId(uuid4()),
        )
        stored = await fx_uow_factory().change_book(
            project_id, lambda repos, _project: repos.page_step_changes.add(change)
        )
        read_back = await fx_uow_factory().read(lambda repos: repos.page_step_changes.get(change.id))
        assert (read_back, stored) == (stored, evolve(change, sequence=1))

    async def test_changes_made_at_the_same_instant_list_in_the_order_they_were_written(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the sequence, not the time or the identifier, orders the history, across transactions and a batch.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        written = [make_page_step_change(page_id=page_id, created_at=EPOCH) for _ in range(6)]

        def add_first_two(repos: Repositories, _project: Project) -> None:
            """Add the first two changes one by one.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            for change in written[:2]:
                repos.page_step_changes.add(change)

        def add_the_rest(repos: Repositories, _project: Project) -> None:
            """Add three changes at once and then the last one.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            repos.page_step_changes.add_many(written[2:5])
            repos.page_step_changes.add(written[5])

        await fx_uow_factory().change_book(project_id, add_first_two)
        await fx_uow_factory().change_book(project_id, add_the_rest)
        listed = await fx_uow_factory().read(lambda repos: repos.page_step_changes.list_for_page(page_id))
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
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        later = make_page_step_change(page_id=page_id, created_at=EPOCH + RECENT)
        earlier = make_page_step_change(page_id=page_id, created_at=EPOCH)
        cleanup = make_page_step_change(page_id=page_id, stage=Stage.CLEANUP, created_at=EPOCH + OLD)
        uow = fx_uow_factory()
        await uow.change_book(
            project_id, lambda repos, _project: repos.page_step_changes.add_many([earlier, later, cleanup])
        )
        listed, in_stage = await uow.read(
            lambda repos: (
                repos.page_step_changes.list_for_page(page_id),
                repos.page_step_changes.list_for_page(page_id, Stage.GEOMETRY),
            )
        )
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
        second_page = make_page(project_id=project_id, order_key=SECOND_ORDER_KEY)
        batch = ChangeBatchId(uuid4())
        members = [
            evolve(make_page_step_change(page_id=page_id), batch_id=batch)
            for page_id in (first_page, second_page.id, first_page)
        ]
        outside = [
            make_page_step_change(page_id=first_page),
            evolve(make_page_step_change(page_id=first_page), batch_id=ChangeBatchId(uuid4())),
        ]

        def add_page_and_changes(repos: Repositories, _project: Project) -> list[PageStepChange]:
            """Add the second page and every change.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            :returns: The changes as stored, with their sequence.
            :rtype: list[PageStepChange]
            """
            repos.pages.add(second_page)
            return list(repos.page_step_changes.add_many([*members, *outside]))

        stored = await fx_uow_factory().change_book(project_id, add_page_and_changes)
        listed = await fx_uow_factory().read(lambda repos: repos.page_step_changes.list_for_batch(batch))
        expected = sorted(stored[: len(members)], key=attrgetter(PAGE_ID, 'sequence'))
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
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        taken, standing, other = (make_page_step_change(page_id=page_id) for _ in range(3))
        undo = evolve(make_page_step_change(page_id=page_id), source=ChangeSource.UNDO, undoes=taken.id)
        unrelated = evolve(make_page_step_change(page_id=page_id), source=ChangeSource.UNDO, undoes=other.id)
        await fx_uow_factory().change_book(
            project_id,
            lambda repos, _project: repos.page_step_changes.add_many([taken, standing, other, undo, unrelated]),
        )
        found, nothing = await fx_uow_factory().read(
            lambda repos: (
                repos.page_step_changes.list_undoing([taken.id, standing.id]),
                repos.page_step_changes.list_undoing([]),
            )
        )
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
        other_page = make_page(project_id=project_id, order_key=SECOND_ORDER_KEY)
        doomed = [make_page_step_change(page_id=page_id) for _ in range(3)]
        undo = evolve(make_page_step_change(page_id=page_id), source=ChangeSource.UNDO, undoes=doomed[0].id)
        carried = evolve(
            make_page_step_change(page_id=page_id), source=ChangeSource.CARRY_OVER, batch_id=ChangeBatchId(uuid4())
        )
        other_step = evolve(make_page_step_change(page_id=page_id), step_id=StepId(uuid4()))
        other_stage = make_page_step_change(page_id=page_id, stage=Stage.CLEANUP)
        elsewhere = make_page_step_change(page_id=other_page.id)

        def add_page_and_changes(repos: Repositories, _project: Project) -> None:
            """Add the other page and every change.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            repos.pages.add(other_page)
            repos.page_step_changes.add_many([*doomed, undo, carried, other_step, other_stage, elsewhere])

        await fx_uow_factory().change_book(project_id, add_page_and_changes)
        step = PageStepKey(page_id, Stage.GEOMETRY, DESKEW_STEP_ID)
        deleted = await fx_uow_factory().change_book(
            project_id, lambda repos, _project: repos.page_step_changes.delete_for_step(step)
        )
        left, left_elsewhere = await fx_uow_factory().read(
            lambda repos: (
                repos.page_step_changes.list_for_page(page_id),
                repos.page_step_changes.list_for_page(other_page.id),
            )
        )
        assert (deleted, [change.id for change in left], [change.id for change in left_elsewhere]) == (
            5,
            [other_step.id, other_stage.id],
            [elsewhere.id],
        )

    async def test_delete_for_step_of_a_step_with_no_history_deletes_nothing(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a step with no change on the page is a count of zero and not an error.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        step = PageStepKey(page_id, Stage.GEOMETRY, DESKEW_STEP_ID)
        deleted = await fx_uow_factory().change_book(
            project_id, lambda repos, _project: repos.page_step_changes.delete_for_step(step)
        )
        assert deleted == 0

    async def test_a_change_added_after_delete_for_step_is_numbered_above_every_remaining_one(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the sequence of the page goes on above the highest one it still holds, whichever changes were deleted.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        other_step = StepId(uuid4())
        written = [
            make_page_step_change(page_id=page_id),
            evolve(make_page_step_change(page_id=page_id), step_id=other_step),
            make_page_step_change(page_id=page_id),
        ]
        step = PageStepKey(page_id, Stage.GEOMETRY, DESKEW_STEP_ID)
        await fx_uow_factory().change_book(
            project_id, lambda repos, _project: repos.page_step_changes.add_many(written)
        )

        def delete_step_and_add(repos: Repositories, _project: Project) -> PageStepChange:
            """Delete the changes of the first step and add another change.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            :returns: The added change as stored.
            :rtype: PageStepChange
            """
            repos.page_step_changes.delete_for_step(step)
            return repos.page_step_changes.add(make_page_step_change(page_id=page_id))

        added = await fx_uow_factory().change_book(project_id, delete_step_and_add)
        listed = await fx_uow_factory().read(lambda repos: repos.page_step_changes.list_for_page(page_id))
        assert ([change.sequence for change in listed], added.sequence) == ([2, 3], 3)

    async def test_change_of_a_missing_page_is_not_found(self, fx_uow_factory: UnitOfWorkFactory) -> None:
        """Reject a change whose page is not stored.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        """
        uow = fx_uow_factory()
        change = make_page_step_change(page_id=PageId(new_account_id()))
        with pytest.raises(NotFoundError):
            await uow.change(lambda repos: repos.page_step_changes.add(change))

    async def test_change_is_stored_once(self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory) -> None:
        """Verify a change with an identifier that is stored already is a conflict.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        change = make_page_step_change(page_id=page_id)
        await fx_uow_factory().change_book(project_id, lambda repos, _project: repos.page_step_changes.add(change))
        with pytest.raises(ConflictError):
            await fx_uow_factory().change_book(project_id, lambda repos, _project: repos.page_step_changes.add(change))


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
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        base = make_page_version(page_id=page_id)
        version = evolve(
            _version(base, scale=VersionScale.PREVIEW, edit_hash='0123456789abcdef', tiles_ready=True),
            review=ReviewReason.LOW_CONFIDENCE,
        )
        await fx_uow_factory().change_book(
            project_id, lambda repos, _project: repos.page_versions.add_many([base, version])
        )
        assert await fx_uow_factory().read(lambda repos: repos.page_versions.get(version.id)) == version

    async def test_find_returns_a_version_by_its_identifier_or_none(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a repeated run finds the version of the earlier run, and a new one finds nothing.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = fx_uow_factory()
        base = make_page_version(page_id=page_id)
        await uow.change_book(project_id, lambda repos, _project: repos.page_versions.add(base))
        found = await uow.read(
            lambda repos: (repos.page_versions.find(base.id), repos.page_versions.find(MISSING_VERSION_ID))
        )
        assert found == (base, None)

    async def test_list_by_ids_reads_the_versions_found_and_leaves_out_the_others(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify one read gives the stored versions among the identifiers, the earliest first.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = fx_uow_factory()
        older = make_page_version(page_id=page_id, minutes=1)
        newer = make_page_version(page_id=page_id, minutes=2)
        unwanted = make_page_version(page_id=page_id, minutes=3)
        await uow.change_book(
            project_id, lambda repos, _project: repos.page_versions.add_many([newer, older, unwanted])
        )
        found = await uow.read(lambda repos: repos.page_versions.list_by_ids([newer.id, older.id, MISSING_VERSION_ID]))
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
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = fx_uow_factory()
        base = make_page_version(page_id=page_id)
        first = evolve(_version(base), created_at=EPOCH + timedelta(minutes=1))
        second = evolve(_version(base), created_at=EPOCH + timedelta(minutes=2))
        preview = evolve(_version(base, scale=VersionScale.PREVIEW), created_at=EPOCH + timedelta(minutes=3))
        cleanup = evolve(_version(base, stage=Stage.CLEANUP), created_at=EPOCH + timedelta(minutes=4))
        await uow.change_book(
            project_id,
            lambda repos, _project: repos.page_versions.add_many([base, first, second, preview, cleanup]),
        )
        full_geometry, previews, window = await uow.read(
            lambda repos: (
                repos.page_versions.list_for_stage(page_id, Stage.GEOMETRY, VersionScale.FULL, EVERYTHING),
                repos.page_versions.list_for_stage(page_id, None, VersionScale.PREVIEW, EVERYTHING),
                repos.page_versions.list_for_stage(page_id, Stage.GEOMETRY, None, SliceRequest(offset=1, limit=2)),
            )
        )
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
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = fx_uow_factory()
        base = make_page_version(page_id=page_id)
        bad = evolve(_version(base), mark=ResultMark.BAD, created_at=EPOCH + timedelta(minutes=1))
        good = evolve(_version(base), mark=ResultMark.GOOD, created_at=EPOCH + timedelta(minutes=2))
        unmarked = evolve(_version(base), created_at=EPOCH + timedelta(minutes=3))
        await uow.change_book(
            project_id, lambda repos, _project: repos.page_versions.add_many([base, bad, good, unmarked])
        )
        listed_bad, listed_good, listed_all = await uow.read(
            lambda repos: (
                repos.page_versions.list_for_stage(page_id, Stage.GEOMETRY, None, EVERYTHING, ResultMark.BAD),
                repos.page_versions.list_for_stage(page_id, None, VersionScale.FULL, EVERYTHING, ResultMark.GOOD),
                repos.page_versions.list_for_stage(page_id, Stage.GEOMETRY, None, EVERYTHING),
            )
        )
        assert (
            [version.id for version in listed_bad.items],
            [version.id for version in listed_good.items],
            [version.id for version in listed_all.items],
        ) == ([bad.id], [good.id], [bad.id, good.id, unmarked.id])

    async def test_collectable_is_non_base_and_outside_the_current_chains(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a collection may delete versions that no page shows and no chain of a shown version needs.

        The base version stays, a head and the version it was computed from stay, and a young preview stays. A full
        run that nothing refers to goes however recent it is, and so does an old preview.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = fx_uow_factory()
        base = evolve(make_page_version(page_id=page_id), created_at=EPOCH)
        feeder = _version(base)
        head = evolve(_version(base, stage=Stage.CLEANUP), input_id=feeder.id)
        orphan = _version(base)
        recent = evolve(_version(base), created_at=NOW - RECENT)
        old_preview = _version(base, scale=VersionScale.PREVIEW)
        young_preview = evolve(_version(base, scale=VersionScale.PREVIEW), created_at=NOW - timedelta(minutes=5))

        def add_versions_and_head(repos: Repositories, _project: Project) -> None:
            """Add the versions and the record of the cleanup stage that names the head.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            repos.page_versions.add_many([base, feeder, head, orphan, recent, old_preview, young_preview])
            repos.page_stages.save(make_page_stage(page_id=page_id, stage=Stage.CLEANUP, head_version_id=head.id))

        await uow.change_book(project_id, add_versions_and_head)
        found = await uow.read(lambda repos: repos.page_versions.collectable(project_id, PREVIEW_CUTOFF))
        assert {version.id for version in found} == {orphan.id, recent.id, old_preview.id}

    async def test_collectable_keeps_a_version_marked_good_or_commented_with_its_inputs(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a collection leaves a version the user marked Good or wrote on, and the inputs it reads.

        A version marked Bad with no comment is no different from an unjudged one, and goes.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = fx_uow_factory()
        base = evolve(make_page_version(page_id=page_id), created_at=EPOCH)
        input_of_good = _version(base)
        good = evolve(_version(base), input_id=input_of_good.id, mark=ResultMark.GOOD)
        commented = evolve(_version(base), comment='The margins are right.')
        commented_bad = evolve(_version(base), mark=ResultMark.BAD, comment='Too dark.')
        plain_bad = evolve(_version(base), mark=ResultMark.BAD)
        unjudged = _version(base)
        versions = [base, input_of_good, good, commented, commented_bad, plain_bad, unjudged]
        await uow.change_book(project_id, lambda repos, _project: repos.page_versions.add_many(versions))
        found = await uow.read(lambda repos: repos.page_versions.collectable(project_id, PREVIEW_CUTOFF))
        assert {version.id for version in found} == {plain_bad.id, unjudged.id}

    async def test_collectable_keeps_the_inputs_of_every_version_that_stays(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify an input goes only with the versions that read it, and not from under one that stays.

        The current version of a stage reads an input, which the database would leave it without if the input were
        deleted, and the input of that input stays too. A chain of versions that nothing outside it reads goes whole.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        uow = fx_uow_factory()
        base = evolve(make_page_version(page_id=page_id), created_at=EPOCH)
        first = _version(base)
        second = evolve(_version(base), input_id=first.id)
        reader = evolve(_version(base, stage=Stage.CLEANUP), input_id=second.id)
        chain_start = _version(base)
        chain_end = evolve(_version(base), input_id=chain_start.id)

        def add_versions_and_reader(repos: Repositories, _project: Project) -> None:
            """Add the versions and the record of the cleanup stage that names the reader.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            repos.page_versions.add_many([base, first, second, reader, chain_start, chain_end])
            repos.page_stages.save(make_page_stage(page_id=page_id, stage=Stage.CLEANUP, head_version_id=reader.id))

        await uow.change_book(project_id, add_versions_and_reader)
        found = await uow.read(lambda repos: repos.page_versions.collectable(project_id, PREVIEW_CUTOFF))
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
        project_id, page_id = await _store_page(fx_uow_factory, fx_new_owner)
        base = make_page_version(page_id=page_id)
        doomed = [_version(base), _version(base)]
        ids = [*(version.id for version in doomed), MISSING_VERSION_ID]
        await fx_uow_factory().change_book(
            project_id, lambda repos, _project: repos.page_versions.add_many([base, *doomed])
        )
        await fx_uow_factory().change_book(project_id, lambda repos, _project: repos.page_versions.delete_many(ids))
        left = await fx_uow_factory().read(lambda repos: repos.page_versions.list_for_page(page_id))
        assert [version.id for version in left] == [base.id]


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
        with_params = evolve(make_job(project_id=project_id), params={'stage': 'geometry', 'page_ids': []})
        await fx_uow_factory().change(lambda repos: repos.jobs.add(with_params))
        stored = await fx_uow_factory().read(lambda repos: repos.jobs.get(with_params.id))
        assert stored.params == with_params.params
