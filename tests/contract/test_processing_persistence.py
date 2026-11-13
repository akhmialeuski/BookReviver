"""Contract of the persistence ports of processing: recipes, stage records, the state and history of steps and page versions.

Every test runs against the SQL adapter, so it keeps the promises of the ports: the keys it checks, the actions of the
foreign keys, and the queries a run and a collection make.
"""

from datetime import timedelta
from functools import partial
from operator import attrgetter
from typing import TYPE_CHECKING, NamedTuple
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.errors import ConflictError, NotFoundError, StepTypeChangedError
from bookreviver.domain.geometry import Line, Point
from bookreviver.domain.history import ChangeSource, PageHistoryEntry
from bookreviver.domain.ids import ChangeBatchId, PageHistoryEntryId, PageId, PageVersionId, StepId
from bookreviver.domain.page_steps import PageEdit, PageStepKey, StepLayer
from bookreviver.domain.pages import PageOrigin, RecipeKind
from bookreviver.domain.recipes import Step, StepTypeChange
from bookreviver.domain.slices import SliceRequest
from bookreviver.domain.stage_summaries import StepTally
from bookreviver.domain.stages import PageStageKey, ReviewReason, Stage, StageState
from bookreviver.domain.step_values import ValueScope
from bookreviver.domain.versions import ProcessorRef, ResultMark, VersionScale, VersionState
from tests.harness.builders.domain import (
    DESKEW,
    DESKEW_STEP_ID,
    EPOCH,
    make_page,
    make_page_edit,
    make_page_history_entry,
    make_page_stage,
    make_page_step_state,
    make_page_version,
    make_recipe,
    make_record,
    make_step_values,
    new_account_id,
    new_book_id,
)
from tests.harness.builders.seeding import store_book

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.books import BookRecord
    from bookreviver.domain.ids import BookId, RecipeId
    from bookreviver.domain.metadata import MetadataMap
    from bookreviver.domain.page_steps import PageStepState
    from bookreviver.domain.pages import Page
    from bookreviver.domain.recipes import Recipe
    from bookreviver.domain.stages import PageStage
    from bookreviver.domain.step_values import StepValues
    from bookreviver.domain.versions import PageVersion
    from bookreviver.ports.persistence import BookDatabase, BookRepositories
    from tests.contract.conftest import OwnerFactory
    from tests.harness.adapters.database import BookDatabasesFactory, CatalogueDatabaseFactory, LocatedBookDatabases

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
# The crop processor, which the tests of the types of a step replace the deskew with, and one of its parameters
CROP_KEY: str = 'geometry.crop'
CROP_MARGIN: str = 'margin'
# The field the content of a change of the type of a step names the type by
TYPE_FIELD: str = 'processor_key'
# The widest window of versions or states a test reads
EVERYTHING: SliceRequest = SliceRequest(limit=10)
# The attribute the records of the pages are sorted by, and the key of a processor that blanks pages
PAGE_ID: str = 'page_id'
BLANK_PROCESSOR: str = 'pages.blank'
# The order key of a second page, after the page every test of the history stores
OTHER_ORDER_KEY: str = 'z0'


def _save_records(repos: BookRepositories, _book_record: BookRecord, *, records: Sequence[PageStage]) -> None:
    """Save stage records one after another, as a run that advances several stages does.

    :param repos: Repositories of the transaction.
    :type repos: BookRepositories
    :param _book_record: Record of the book, which the work does not need.
    :type _book_record: BookRecord
    :param records: Records to save.
    :type records: Sequence[PageStage]
    """
    for record in records:
        repos.page_stages.save(record)


def _save_values(repos: BookRepositories, _book_record: BookRecord, *, values: Sequence[StepValues]) -> None:
    """Save the values of steps one after another.

    :param repos: Repositories of the transaction.
    :type repos: BookRepositories
    :param _book_record: Record of the book, which the work does not need.
    :type _book_record: BookRecord
    :param values: Values to save.
    :type values: Sequence[StepValues]
    """
    for part in values:
        repos.step_values.save(part)


def _add_head(repos: BookRepositories, _book_record: BookRecord, *, version: PageVersion, record: PageStage) -> None:
    """Add a version and the stage record that names it as the current version.

    :param repos: Repositories of the transaction.
    :type repos: BookRepositories
    :param _book_record: Record of the book, which the work does not need.
    :type _book_record: BookRecord
    :param version: Version to add.
    :type version: PageVersion
    :param record: Record naming the version.
    :type record: PageStage
    """
    repos.page_versions.add(version)
    repos.page_stages.save(record)


async def _store_page(
    catalogue_factory: CatalogueDatabaseFactory, new_owner: OwnerFactory, *, databases: LocatedBookDatabases
) -> tuple[BookId, PageId]:
    """Store a book with one page, each in the change that owns it.

    :param catalogue_factory: Function opening a new catalogue database over the database of the test.
    :type catalogue_factory: CatalogueDatabaseFactory
    :param new_owner: Function creating an account the database accepts as an owner.
    :type new_owner: OwnerFactory
    :param databases: Opens the databases of the books of the test.
    :type databases: LocatedBookDatabases
    :returns: The identifiers of the book and its page.
    :rtype: tuple[BookId, PageId]
    """
    page = make_page()
    book_id = await store_book(catalogue_factory(), make_record(), page, owner=await new_owner(), databases=databases)
    return book_id, page.id


async def _new_step(
    book_id: BookId,
    *,
    databases: LocatedBookDatabases,
    stage: Stage = Stage.GEOMETRY,
    kind: RecipeKind = RecipeKind.BLANK,
) -> StepId:
    """Store a recipe of one kind of a stage holding a new step, which the rows of a page can name.

    :param book_id: Identifier of the book.
    :type book_id: BookId
    :param databases: Opens the databases of the books of the test.
    :type databases: LocatedBookDatabases
    :param stage: Stage of the recipe and of the step.
    :type stage: Stage
    :param kind: Kind of the recipe, which no other recipe of the stage in the book may have.
    :type kind: RecipeKind
    :returns: The identifier of the new step.
    :rtype: StepId
    """
    recipe = evolve(make_recipe(stage=stage, kind=kind), steps=(Step(processor_key=DESKEW.key),))
    await databases.open(book_id).change(lambda repos, _book_record: repos.recipes.add(recipe))
    return recipe.steps[0].step_id


def _blank_page(order_key: str) -> Page:
    """Build a page that has an image, a blank leaf, since a placeholder has none.

    :param order_key: Order key placing the page in the book.
    :type order_key: str
    :returns: The page.
    :rtype: Page
    """
    return evolve(make_page(order_key=order_key), origin=PageOrigin.BLANK)


async def _add_marked_head(book_id: BookId, page: Page, *, databases: LocatedBookDatabases) -> PageVersionId:
    """Store a base version of a page and a version of the geometry stage marked for review.

    :param book_id: Identifier of the book of the page.
    :type book_id: BookId
    :param page: The page, which is stored.
    :type page: Page
    :param databases: Opens the databases of the books of the test.
    :type databases: LocatedBookDatabases
    :returns: The identifier of the marked version.
    :rtype: PageVersionId
    """
    base = make_page_version(page_id=page.id)
    head = evolve(_version(base), review=ReviewReason.LOW_CONFIDENCE)
    await databases.open(book_id).change(lambda repos, _book_record: repos.page_versions.add_many([base, head]))
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
    """Tests for the recipes of a book."""

    async def test_stage_has_one_recipe_for_each_kind(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Reject a second recipe of a kind in a stage, which two requests making the recipes could both store.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        catalogue = fx_catalogue()
        book_id = await store_book(catalogue, make_record(), owner=await fx_new_owner(), databases=fx_databases())
        first = make_recipe(kind=RecipeKind.BLANK)
        second = make_recipe(kind=RecipeKind.BLANK, minutes=1)
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.recipes.add(first))
        with pytest.raises(ConflictError):
            await fx_databases().open(book_id).change(lambda repos, _book_record: repos.recipes.add(second))

    async def test_every_kind_and_other_stages_may_be_added_beside_a_recipe(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the one recipe of a kind is per stage, and the recipes of the stage are listed by creation.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        catalogue = fx_catalogue()
        book_id = await store_book(catalogue, make_record(), owner=await fx_new_owner(), databases=fx_databases())
        recipes = [make_recipe(kind=kind, minutes=index) for index, kind in enumerate(RecipeKind)]
        other_stage = make_recipe(stage=Stage.CLEANUP)
        await (
            fx_databases()
            .open(book_id)
            .change(lambda repos, _book_record: repos.recipes.add_many([*reversed(recipes), other_stage]))
        )
        listed = await fx_databases().open(book_id).read(lambda repos: repos.recipes.list_for_stage(Stage.GEOMETRY))
        assert [recipe.id for recipe in listed] == [recipe.id for recipe in recipes]

    async def test_list_for_book_returns_the_recipes_of_each_stage_in_pipeline_order(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the recipes of another book are left out, and the stages keep the pipeline order.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        owner_id = await fx_new_owner()
        cleanup = make_recipe(stage=Stage.CLEANUP)
        geometry = make_recipe()
        picture = make_recipe(kind=RecipeKind.BW_PICTURE, minutes=1)
        elsewhere = make_recipe()
        catalogue = fx_catalogue()
        book_id, other_id = [
            await store_book(catalogue, make_record(), owner=owner_id, databases=fx_databases()) for _ in range(2)
        ]
        await (
            fx_databases()
            .open(book_id)
            .change(lambda repos, _book_record: repos.recipes.add_many([cleanup, picture, geometry]))
        )
        await fx_databases().open(other_id).change(lambda repos, _book_record: repos.recipes.add(elsewhere))
        listed = await fx_databases().open(book_id).read(lambda repos: repos.recipes.list_all())
        assert [recipe.id for recipe in listed] == [geometry.id, picture.id, cleanup.id]

    async def test_steps_survive_the_store(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a recipe reads back with its steps, their parameters, identifiers and switches.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        catalogue = fx_catalogue()
        plain = make_recipe()
        other = Step(processor_key='geometry.other', params={}, enabled=False)
        recipe = evolve(plain, steps=(*plain.steps, other, evolve(plain.steps[0], step_id=StepId(uuid4()))))
        book_id = await store_book(catalogue, make_record(), owner=await fx_new_owner(), databases=fx_databases())
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.recipes.add(recipe))
        assert await fx_databases().open(book_id).read(lambda repos: repos.recipes.get(recipe.id)) == recipe

    async def test_recipe_of_a_missing_book_is_not_found(self, fx_databases: BookDatabasesFactory) -> None:
        """Reject a recipe of a book that is not stored, which has no file to hold it.

        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        recipe = make_recipe()
        with pytest.raises(NotFoundError):
            await fx_databases().open(new_book_id()).change(lambda repos, _book_record: repos.recipes.add(recipe))

    async def test_deleting_a_recipe_leaves_its_pages_without_it(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the stage record of a page keeps its row and loses the recipe that is deleted.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        recipe = make_recipe()

        def add_recipe_and_record(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Add the recipe and a stage record that names it.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            repos.recipes.add(recipe)
            repos.page_stages.save(make_page_stage(page_id=page_id, recipe_id=recipe.id))

        await fx_databases().open(book_id).change(add_recipe_and_record)
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.recipes.delete(recipe.id))
        kept = (
            await fx_databases()
            .open(book_id)
            .read(lambda repos: repos.page_stages.get(PageStageKey(page_id, Stage.GEOMETRY)))
        )
        assert kept.recipe_id is None

    async def test_deleting_the_book_deletes_its_recipes(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the recipes go with their book.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, _ = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        recipe = make_recipe()
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.recipes.add(recipe))
        await fx_databases().delete_book(book_id)
        with pytest.raises(NotFoundError):
            await fx_databases().open(book_id).read(lambda repos: repos.recipes.get(recipe.id))


def _three_steps() -> Recipe:
    """Build a recipe of the geometry stage with three steps of the deskew processor, which differ by their angle.

    :returns: The recipe.
    :rtype: Recipe
    """
    steps = tuple(Step(processor_key=DESKEW.key, params={MAX_ANGLE: angle}) for angle in range(1, 4))
    return evolve(make_recipe(), steps=steps)


class TestRecipeSteps:
    """Tests for the steps of a recipe, each of which is a step of the book that the recipe places in its order."""

    async def test_a_moved_step_keeps_its_identifier_and_its_parameters(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a recipe whose steps are reordered reads back in the new order, every step as it was.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, _ = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        recipe = _three_steps()
        first, second, third = recipe.steps
        moved = evolve(recipe, steps=(third, first, second))
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.recipes.add(recipe))
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.recipes.update(moved))
        moved_back = evolve(recipe, steps=(first, third, second))
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.recipes.update(moved_back))
        stored = await fx_databases().open(book_id).read(lambda repos: repos.recipes.get(recipe.id))
        assert stored == moved_back

    async def test_a_removed_step_keeps_what_the_pages_hold_for_it(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a step taken out of its recipe stays a step of the book, whose settings, values and history remain.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        recipe = _three_steps()
        gone = recipe.steps[1].step_id
        state = make_page_step_state(page_id=page_id, step_id=gone, params={MAX_ANGLE: 4})
        values = make_step_values(step_id=gone, scope=ValueScope.ODD)

        def add_with_state(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Store the recipe and what the page keeps for its second step.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            repos.recipes.add(recipe)
            repos.page_step_states.save(state)

        await fx_databases().open(book_id).change(add_with_state)
        shorter = evolve(recipe, steps=(recipe.steps[0], recipe.steps[2]))
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.recipes.update(shorter))
        # The step left every recipe, and the book still takes rows that name it
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.step_values.save(values))
        stored, kept, listed = (
            await fx_databases()
            .open(book_id)
            .read(
                lambda repos: (
                    repos.recipes.get(recipe.id),
                    repos.page_step_states.find(state.key),
                    repos.step_values.list_for_step(gone),
                )
            )
        )
        expect(stored == shorter)
        expect(kept == state)
        expect(listed == [values])
        assert_expectations()

    async def test_a_returned_step_is_found_again_with_what_the_pages_hold_for_it(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a step put back into its recipe under its identifier is the step it was, with its new parameters.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        recipe = _three_steps()
        first, second, third = recipe.steps
        state = make_page_step_state(page_id=page_id, step_id=second.step_id, params={MAX_ANGLE: 4})

        def add_with_state(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Store the recipe and what the page keeps for its second step.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            repos.recipes.add(recipe)
            repos.page_step_states.save(state)

        await fx_databases().open(book_id).change(add_with_state)
        shorter = evolve(recipe, steps=(first, third))
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.recipes.update(shorter))
        returned = evolve(recipe, steps=(first, third, evolve(second, params={MAX_ANGLE: 9}, enabled=False)))
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.recipes.update(returned))
        stored, kept = (
            await fx_databases()
            .open(book_id)
            .read(lambda repos: (repos.recipes.get(recipe.id), repos.page_step_states.find(state.key)))
        )
        expect(stored == returned)
        expect(kept == state)
        assert_expectations()

    async def test_a_step_of_one_stage_is_refused_in_a_recipe_of_another(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a step belongs to the stage of the recipe that first held it, and no recipe of another stage holds it.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, _ = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        geometry = _three_steps()
        cleanup = evolve(make_recipe(stage=Stage.CLEANUP), steps=(geometry.steps[0],))
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.recipes.add(geometry))
        with pytest.raises(NotFoundError):
            await fx_databases().open(book_id).change(lambda repos, _book_record: repos.recipes.add(cleanup))

    async def test_a_step_two_recipes_of_a_stage_hold_stays_in_the_one_that_keeps_it(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify recipes of two kinds may hold one step, each with its own parameters, and one may let it go.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, _ = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        text = _three_steps()
        shared = text.steps[0]
        blank = evolve(make_recipe(kind=RecipeKind.BLANK, minutes=1), steps=(evolve(shared, params={MAX_ANGLE: 7}),))
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.recipes.add_many([text, blank]))
        without = evolve(text, steps=text.steps[1:])
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.recipes.update(without))
        stored = await fx_databases().open(book_id).read(lambda repos: repos.recipes.list_for_stage(Stage.GEOMETRY))
        assert list(stored) == [without, blank]


def _shared_step() -> tuple[Recipe, Recipe]:
    """Build the recipes of text pages and of blank pages of the geometry, which hold one deskew step of their own angles.

    :returns: The recipe of text pages and the recipe of blank pages.
    :rtype: tuple[Recipe, Recipe]
    """
    text = evolve(make_recipe(), steps=(Step(processor_key=DESKEW.key, params={MAX_ANGLE: 1}),))
    blank = evolve(make_recipe(kind=RecipeKind.BLANK, minutes=1), steps=(evolve(text.steps[0], params={MAX_ANGLE: 2}),))
    return text, blank


def _retyped(recipe: Recipe, key: str, params: MetadataMap) -> Recipe:
    """Give the one step of a recipe another type, as the recipe reads back after the replacement.

    :param recipe: A recipe of one step.
    :type recipe: Recipe
    :param key: Key of the processor of the new type.
    :type key: str
    :param params: Parameters of the step under the new type.
    :type params: MetadataMap
    :returns: The recipe with the step of the new type, changed at ``NOW``.
    :rtype: Recipe
    """
    return evolve(recipe, steps=(evolve(recipe.steps[0], processor_key=key, params=params),), updated_at=NOW)


class KeptRows(NamedTuple):
    """What the pages keep for the one step of two recipes under its first type, and the change that replaced it.

    :ivar recipes: The recipes of text pages and of blank pages, which hold the step.
    :ivar state: The setting of the page.
    :ivar odd: The values of the odd pages.
    :ivar group: The values of a group.
    :ivar retype: The change of the type of the step to the crop, which the history of the page keeps.
    """

    recipes: tuple[Recipe, Recipe]
    state: PageStepState
    odd: StepValues
    group: StepValues
    retype: PageHistoryEntry

    @property
    def step_id(self) -> StepId:
        """The step the recipes hold."""
        return self.recipes[0].steps[0].step_id


def _replace_type(
    repos: BookRepositories, _book_record: BookRecord, *, kept: KeptRows, key: str, changes: Sequence[PageHistoryEntry]
) -> None:
    """Replace the type of the step of the recipes, with no parameters in either, and add changes to the history after.

    :param repos: Repositories of the transaction.
    :type repos: BookRepositories
    :param _book_record: Record of the book, which the work does not need.
    :type _book_record: BookRecord
    :param kept: The recipes and the step.
    :type kept: KeptRows
    :param key: Key of the processor of the new type.
    :type key: str
    :param changes: The changes the history gains once the type is replaced.
    :type changes: Sequence[PageHistoryEntry]
    """
    params: dict[RecipeId, MetadataMap] = {recipe.id: {} for recipe in kept.recipes}
    repos.recipes.replace_type(StepTypeChange(step_id=kept.step_id, processor_key=key, params=params, moment=NOW))
    repos.page_history.add_many(changes)


def _read_kept(repos: BookRepositories, *, kept: KeptRows) -> tuple[object, ...]:
    """Read what the pages keep for the step through every read of the repositories.

    :param repos: Repositories of the transaction.
    :type repos: BookRepositories
    :param kept: The rows written under the first type.
    :type kept: KeptRows
    :returns: The state found, the states of the page, the states of the step, the values of the step and of the book,
              the values of the odd pages, and the layers of the changes of the page.
    :rtype: tuple[object, ...]
    """
    page_id = kept.state.page_id
    return (
        repos.page_step_states.find(kept.state.key),
        list(repos.page_step_states.list_for_page(page_id)),
        list(repos.page_step_states.list_for_step([page_id], Stage.GEOMETRY, kept.step_id)),
        list(repos.step_values.list_for_step(kept.step_id)),
        list(repos.step_values.list_all()),
        repos.step_values.find(kept.odd.key),
        [change.layer for change in repos.page_history.list_for_page(page_id)],
    )


async def _retyped_book(book: BookDatabase, page_id: PageId) -> KeptRows:
    """Store two recipes sharing a step, what the page keeps for it, and then give the step the crop type.

    :param book: The database of the stored book.
    :type book: BookDatabase
    :param page_id: The page of the book.
    :type page_id: PageId
    :returns: The rows written under the deskew type, and the change of the type the history of the page keeps.
    :rtype: KeptRows
    """
    recipes = _shared_step()
    step_id = recipes[0].steps[0].step_id
    retype = evolve(
        make_page_history_entry(page_id=page_id),
        step_id=step_id,
        layer=StepLayer.TYPE,
        before={TYPE_FIELD: DESKEW.key},
        after={TYPE_FIELD: CROP_KEY},
    )
    kept = KeptRows(
        recipes=recipes,
        state=make_page_step_state(page_id=page_id, step_id=step_id, params={MAX_ANGLE: 4}),
        odd=make_step_values(step_id=step_id, scope=ValueScope.ODD),
        group=make_step_values(step_id=step_id, scope=ValueScope.GROUP, group_label=INDEX_GROUP),
        retype=retype,
    )

    def store(repos: BookRepositories, _book_record: BookRecord) -> None:
        """Store the recipes and what the pages keep for the step under its first type.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :param _book_record: Record of the book, which the work does not need.
        :type _book_record: BookRecord
        """
        repos.recipes.add_many(recipes)
        repos.page_step_states.save(kept.state)
        repos.step_values.save_many([kept.odd, kept.group])
        repos.page_history.add(evolve(make_page_history_entry(page_id=page_id), step_id=step_id))

    await book.change(store)
    await book.change(partial(_replace_type, kept=kept, key=CROP_KEY, changes=(retype,)))
    return kept


class TestStepTypes:
    """Tests for the type of a step, which only its replacement changes, and the rows the pages keep for each type."""

    async def test_the_type_of_a_step_two_recipes_hold_is_replaced_in_both_and_its_parameters_come_back(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify both recipes take the new type with their own parameters, and keep those of the earlier type.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, _ = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        text, blank = _shared_step()
        step_id = text.steps[0].step_id
        book = fx_databases().open(book_id)
        await book.change(lambda repos, _book_record: repos.recipes.add_many([text, blank]))
        crop = {text.id: {CROP_MARGIN: 3}, blank.id: {CROP_MARGIN: 4}}
        change = StepTypeChange(step_id=step_id, processor_key=CROP_KEY, params=crop, moment=NOW)
        await book.change(lambda repos, _book_record: repos.recipes.replace_type(change))
        replaced, kept = await book.read(
            lambda repos: (repos.recipes.list_for_stage(Stage.GEOMETRY), repos.recipes.kept_params(step_id, DESKEW.key))
        )
        back = StepTypeChange(step_id=step_id, processor_key=DESKEW.key, params=kept, moment=NOW)
        await book.change(lambda repos, _book_record: repos.recipes.replace_type(back))
        returned, kept_crop = await book.read(
            lambda repos: (repos.recipes.list_for_stage(Stage.GEOMETRY), repos.recipes.kept_params(step_id, CROP_KEY))
        )

        expect(list(replaced) == [_retyped(text, CROP_KEY, crop[text.id]), _retyped(blank, CROP_KEY, crop[blank.id])])
        expect(kept == {text.id: {MAX_ANGLE: 1}, blank.id: {MAX_ANGLE: 2}})
        expect(list(returned) == [evolve(text, updated_at=NOW), evolve(blank, updated_at=NOW)])
        expect(kept_crop == crop)
        assert_expectations()

    async def test_a_recipe_that_names_a_step_under_another_type_is_refused(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a write of a recipe never changes the type of a step it holds, which only the replacement does.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, _ = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        text, _ = _shared_step()
        book = fx_databases().open(book_id)
        await book.change(lambda repos, _book_record: repos.recipes.add(text))
        changed = _retyped(text, CROP_KEY, {CROP_MARGIN: 3})

        with pytest.raises(StepTypeChangedError):
            await book.change(lambda repos, _book_record: repos.recipes.update(changed))
        stored = await book.read(lambda repos: repos.recipes.get(text.id))

        assert stored == text

    async def test_what_the_pages_keep_for_the_earlier_type_is_unread_under_the_new_one(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the state, the values and the history of the earlier type are unread, and the change of the type shows.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        kept = await _retyped_book(fx_databases().open(book_id), page_id)

        under_crop = await fx_databases().open(book_id).read(partial(_read_kept, kept=kept))

        assert under_crop == (None, [], [], [], [], None, [StepLayer.TYPE])

    async def test_what_the_pages_keep_for_a_type_comes_back_with_it(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the rows of the earlier type are read again once it is back, and those of the new type once it is.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        book = fx_databases().open(book_id)
        kept = await _retyped_book(book, page_id)
        crop_state = evolve(kept.state, params={CROP_MARGIN: 2})
        await book.change(lambda repos, _book_record: repos.page_step_states.save(crop_state))
        undo = kept.retype.taken_back(NOW, None)

        await book.change(partial(_replace_type, kept=kept, key=DESKEW.key, changes=(undo,)))
        back = await book.read(partial(_read_kept, kept=kept))
        await book.change(partial(_replace_type, kept=kept, key=CROP_KEY, changes=()))
        crop_again = await book.read(lambda repos: repos.page_step_states.find(kept.state.key))

        layers = [StepLayer.SETTINGS, StepLayer.TYPE, StepLayer.TYPE]
        values = [kept.group, kept.odd]
        expect(back == (kept.state, [kept.state], [kept.state], values, values, kept.odd, layers))
        expect(crop_again == crop_state)
        assert_expectations()

    async def test_delete_for_step_takes_the_changes_of_the_active_type_and_of_the_type_and_keeps_the_others(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the history of an earlier type outlives a delete of the history of the step, and shows once it is back.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        text, _ = _shared_step()
        step_id = text.steps[0].step_id
        key = PageStepKey(page_id, Stage.GEOMETRY, step_id)
        book = fx_databases().open(book_id)
        setting = evolve(make_page_history_entry(page_id=page_id), step_id=step_id)
        retype = evolve(setting, id=PageHistoryEntryId(uuid4()), layer=StepLayer.TYPE)
        crop_setting = evolve(setting, id=PageHistoryEntryId(uuid4()))

        def to(type_key: str) -> StepTypeChange:
            """Give the replacement of the type of the step.

            :param type_key: Key of the processor of the new type.
            :type type_key: str
            :returns: The replacement.
            :rtype: StepTypeChange
            """
            return StepTypeChange(step_id=step_id, processor_key=type_key, params={text.id: {}}, moment=NOW)

        await book.change(lambda repos, _book_record: (repos.recipes.add(text), repos.page_history.add(setting)))
        await book.change(lambda repos, _book_record: repos.recipes.replace_type(to(CROP_KEY)))
        await book.change(lambda repos, _book_record: repos.page_history.add_many([retype, crop_setting]))
        deleted = await book.change(lambda repos, _book_record: repos.page_history.delete_for_step(key))
        after = await book.read(lambda repos: repos.page_history.list_for_page(page_id))
        await book.change(lambda repos, _book_record: repos.recipes.replace_type(to(DESKEW.key)))
        back = await book.read(lambda repos: [change.id for change in repos.page_history.list_for_page(page_id)])

        expect(deleted == 2)
        expect(list(after) == [])
        expect(back == [setting.id])
        assert_expectations()


class TestPageStageRepository:
    """Tests for the current version of each stage of each page."""

    async def test_save_stores_a_record_and_replaces_it(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a record is stored by the first save and replaced by the second, which is how a run advances it.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        stale = make_page_stage(page_id=page_id, state=StageState.STALE)

        def save_twice(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Save a record of the stage and then a stale one in its place.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            repos.page_stages.save(make_page_stage(page_id=page_id))
            repos.page_stages.save(stale)

        await fx_databases().open(book_id).change(save_twice)
        found = (
            await fx_databases()
            .open(book_id)
            .read(lambda repos: repos.page_stages.find(PageStageKey(page_id, Stage.GEOMETRY)))
        )
        assert found == stale

    async def test_save_many_stores_new_records_and_replaces_stored_ones_in_one_call(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify one call stores two new records and replaces a stored one, and returns them in the order given.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        await (
            fx_databases()
            .open(book_id)
            .change(lambda repos, _book_record: repos.page_stages.save(make_page_stage(page_id=page_id)))
        )
        replaced = make_page_stage(page_id=page_id, state=StageState.STALE)
        batch = [
            make_page_stage(page_id=page_id, stage=Stage.CLEANUP),
            replaced,
            make_page_stage(page_id=page_id, stage=Stage.PAGE_SPLIT),
        ]

        returned = (
            await fx_databases().open(book_id).change(lambda repos, _book_record: repos.page_stages.save_many(batch))
        )
        stored = await fx_databases().open(book_id).read(lambda repos: repos.page_stages.list_for_page(page_id))

        order = list(Stage)
        expect(list(returned) == batch)
        expect(list(stored) == sorted(batch, key=lambda record: order.index(record.stage)))
        assert_expectations()

    async def test_save_many_with_a_record_of_a_missing_page_stores_nothing(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Reject the call and keep its other records out of the book, which the whole call rolls back.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        batch = [make_page_stage(page_id=page_id), make_page_stage(page_id=PageId(new_account_id()))]

        with pytest.raises(NotFoundError):
            await fx_databases().open(book_id).change(lambda repos, _book_record: repos.page_stages.save_many(batch))

        assert await fx_databases().open(book_id).read(lambda repos: repos.page_stages.list_for_page(page_id)) == []

    async def test_find_returns_none_for_a_stage_that_has_not_run(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a missing record is None for find and a NotFoundError for get.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        key = PageStageKey(page_id, Stage.CLEANUP)
        assert await fx_databases().open(book_id).read(lambda repos: repos.page_stages.find(key)) is None
        with pytest.raises(NotFoundError):
            await fx_databases().open(book_id).read(lambda repos: repos.page_stages.get(key))

    async def test_record_of_a_missing_page_is_not_found(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Reject a record whose page is not stored.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, _ = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        record = make_page_stage(page_id=PageId(new_account_id()))
        with pytest.raises(NotFoundError):
            await fx_databases().open(book_id).change(lambda repos, _book_record: repos.page_stages.save(record))

    async def test_list_for_page_follows_the_order_of_the_stages(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the records of a page come in pipeline order, whatever order they were saved in.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())

        def save_out_of_order(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Save a record for each of three stages, the last stage first.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            for stage in (Stage.CLEANUP, Stage.PAGE_SPLIT, Stage.GEOMETRY):
                repos.page_stages.save(make_page_stage(page_id=page_id, stage=stage))

        await fx_databases().open(book_id).change(save_out_of_order)
        listed = await fx_databases().open(book_id).read(lambda repos: repos.page_stages.list_for_page(page_id))
        assert [record.stage for record in listed] == [Stage.PAGE_SPLIT, Stage.GEOMETRY, Stage.CLEANUP]

    async def test_list_for_pages_reads_the_records_of_the_given_pages_only(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify one read gives the records of several pages in pipeline order, and none of the other pages.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        pages = [make_page(order_key=f'a{index}') for index in range(3)]
        first, second, other = (page.id for page in pages)
        catalogue = fx_catalogue()
        book_id = await store_book(
            catalogue, make_record(), *pages, owner=await fx_new_owner(), databases=fx_databases()
        )
        records = [
            make_page_stage(page_id=page.id, stage=stage)
            for page in pages
            for stage in (Stage.GEOMETRY, Stage.PAGE_SPLIT)
        ]
        await fx_databases().open(book_id).change(partial(_save_records, records=records))
        found = await fx_databases().open(book_id).read(lambda repos: repos.page_stages.list_for_pages([first, second]))
        by_page = {page_id: [r.stage for r in found if r.page_id == page_id] for page_id in (first, second, other)}
        assert by_page == {
            first: [Stage.PAGE_SPLIT, Stage.GEOMETRY],
            second: [Stage.PAGE_SPLIT, Stage.GEOMETRY],
            other: [],
        }

    async def test_head_version_and_recipe_are_checked(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Reject a record that names a version or a recipe that is not stored.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        record = make_page_stage(page_id=page_id, head_version_id=PageVersionId('0' * 16))
        with pytest.raises(NotFoundError):
            await fx_databases().open(book_id).change(lambda repos, _book_record: repos.page_stages.save(record))

    async def test_queries_by_recipe_and_by_stage_of_a_book(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the records of a recipe and of a stage are found, and neither another stage nor another book.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        other_book_id, other_page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        recipe = make_recipe()
        mine = make_page_stage(page_id=page_id, recipe_id=recipe.id)
        theirs = make_page_stage(page_id=other_page_id)

        def add_recipe_and_records(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Add the recipe, the record that names it and a record of another stage.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            repos.recipes.add(recipe)
            repos.page_stages.save(mine)
            repos.page_stages.save(make_page_stage(page_id=page_id, stage=Stage.CLEANUP))

        await fx_databases().open(book_id).change(add_recipe_and_records)
        await fx_databases().open(other_book_id).change(lambda repos, _book_record: repos.page_stages.save(theirs))
        found = (
            await fx_databases()
            .open(book_id)
            .read(
                lambda repos: (
                    repos.page_stages.list_for_recipe(recipe.id),
                    repos.page_stages.list_for_stage(Stage.GEOMETRY),
                ),
            )
        )
        assert found == ([mine], [mine])

    async def test_list_fresh_returns_the_fresh_records_of_the_book(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the fresh records are listed in the order of the stages, and no stale or failed one is in.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        fresh = [
            make_page_stage(page_id=page_id, stage=Stage.CLEANUP),
            make_page_stage(page_id=page_id, stage=Stage.GEOMETRY),
        ]
        stale = make_page_stage(page_id=page_id, stage=Stage.PAGE_SPLIT, state=StageState.STALE)
        failed = make_page_stage(page_id=page_id, stage=Stage.PAGE_ORDER, state=StageState.FAILED)
        await fx_databases().open(book_id).change(partial(_save_records, records=[*fresh, stale, failed]))
        order = list(Stage)

        listed = await fx_databases().open(book_id).read(lambda repos: repos.page_stages.list_fresh())

        assert list(listed) == sorted(fresh, key=lambda record: order.index(record.stage))

    async def test_list_replaced_returns_the_records_made_by_another_version_of_the_processor_in_any_state(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a stage's records of the book are listed when their version is of the key but not the given one.

        A record of the installed version, one of another processor and one of another stage are left out, and the
        state of the record does not matter.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        installed = ProcessorRef(key=BLANK_PROCESSOR, version='2')
        books = [await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases()) for _ in range(4)]
        pages = [page_id for _, page_id in books]
        made_by = [
            ProcessorRef(key=BLANK_PROCESSOR, version='1'),
            installed,
            ProcessorRef(key='geometry.crop', version='1'),
            ProcessorRef(key=BLANK_PROCESSOR, version='1'),
        ]
        records = []
        for (book_id, page_id), processor in zip(books, made_by, strict=True):
            version = evolve(make_page_version(page_id=page_id), stage=Stage.PAGE_ORDER, processor=processor)
            state = StageState.STALE if page_id == pages[0] else StageState.FRESH
            record = make_page_stage(page_id=page_id, stage=Stage.PAGE_ORDER, head_version_id=version.id, state=state)
            records.append(record)
            await fx_databases().open(book_id).change(partial(_add_head, version=version, record=record))
        other_stage = evolve(make_page_version(page_id=pages[3]), stage=Stage.GEOMETRY, processor=made_by[0])
        other_record = make_page_stage(page_id=pages[3], stage=Stage.GEOMETRY, head_version_id=other_stage.id)
        await fx_databases().open(books[3][0]).change(partial(_add_head, version=other_stage, record=other_record))

        found = [
            await fx_databases()
            .open(book_id)
            .read(lambda repos: repos.page_stages.list_replaced(Stage.PAGE_ORDER, installed))
            for book_id, _ in books
        ]

        assert found == [[records[0]], [], [], [records[3]]]

    async def test_head_ids_lists_the_current_versions_of_a_book(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the identifiers of the head versions of the book's records are listed once each.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        base = make_page_version(page_id=page_id)
        version = _version(base)

        def add_versions_and_records(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Add the versions and two records, one of which names the version as its head.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            repos.page_versions.add_many([base, version])
            repos.page_stages.save(make_page_stage(page_id=page_id, head_version_id=version.id))
            repos.page_stages.save(make_page_stage(page_id=page_id, stage=Stage.CLEANUP))

        await fx_databases().open(book_id).change(add_versions_and_records)
        assert set(await fx_databases().open(book_id).read(lambda repos: repos.page_stages.head_ids())) == {version.id}

    async def test_tally_counts_the_records_of_each_stage_by_state_and_review(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify one grouped count gives each stage its states and marks, and leaves out what does not count.

        A mark on a failed page, a placeholder and the pages of another book are not counted.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        owner_id = await fx_new_owner()
        catalogue = fx_catalogue()
        marked, stale, failed = (_blank_page(f'a{number}') for number in range(3))
        placeholder = make_page(order_key='b0')
        elsewhere = _blank_page('a0')
        book_id = await store_book(
            catalogue, make_record(), marked, stale, failed, placeholder, owner=owner_id, databases=fx_databases()
        )
        other_id = await store_book(catalogue, make_record(), elsewhere, owner=owner_id, databases=fx_databases())
        marked_head = await _add_marked_head(book_id, marked, databases=fx_databases())
        failed_head = await _add_marked_head(book_id, failed, databases=fx_databases())
        records = [
            make_page_stage(page_id=marked.id, head_version_id=marked_head),
            make_page_stage(page_id=stale.id, state=StageState.STALE),
            make_page_stage(page_id=failed.id, head_version_id=failed_head, state=StageState.FAILED),
            make_page_stage(page_id=placeholder.id),
            make_page_stage(page_id=marked.id, stage=Stage.CLEANUP),
        ]
        await fx_databases().open(book_id).change(partial(_save_records, records=records))
        await (
            fx_databases()
            .open(other_id)
            .change(lambda repos, _book_record: repos.page_stages.save(make_page_stage(page_id=elsewhere.id)))
        )
        tallies = await fx_databases().open(book_id).read(lambda repos: repos.page_stages.tally())
        counts = {(tally.stage, tally.fresh, tally.stale, tally.failed, tally.review, tally.check) for tally in tallies}
        assert counts == {(Stage.GEOMETRY, 1, 1, 1, 1, 3), (Stage.CLEANUP, 1, 0, 0, 0, 0)}

    async def test_tally_counts_a_page_both_stale_and_marked_once_in_check(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a page that is stale and marked adds to check once, and a fresh page with no mark adds nothing.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        catalogue = fx_catalogue()
        both, clean = (_blank_page(f'a{number}') for number in range(2))
        book_id = await store_book(
            catalogue, make_record(), both, clean, owner=await fx_new_owner(), databases=fx_databases()
        )
        head_id = await _add_marked_head(book_id, both, databases=fx_databases())
        records = [
            make_page_stage(page_id=both.id, head_version_id=head_id, state=StageState.STALE),
            make_page_stage(page_id=clean.id),
        ]
        await fx_databases().open(book_id).change(partial(_save_records, records=records))
        [tally] = await fx_databases().open(book_id).read(lambda repos: repos.page_stages.tally())
        assert (tally.fresh, tally.stale, tally.review, tally.check) == (1, 1, 1, 1)

    async def test_step_tally_counts_the_pages_stopped_at_each_step(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the pages a run stopped at a step are counted by step, and partial in the tally of the stage.

        A page run through every step, a failed record, a placeholder and the pages of another book are left out.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        owner_id = await fx_new_owner()
        catalogue = fx_catalogue()
        first, second, whole, failed = (_blank_page(f'a{number}') for number in range(4))
        placeholder = make_page(order_key='b1')
        elsewhere = _blank_page('a1')
        book_id = await store_book(
            catalogue,
            make_record(),
            first,
            second,
            whole,
            failed,
            placeholder,
            owner=owner_id,
            databases=fx_databases(),
        )
        other_id = await store_book(catalogue, make_record(), elsewhere, owner=owner_id, databases=fx_databases())
        stopped = [
            evolve(make_page_stage(page_id=page.id, state=state), through_step_id=through_step_id)
            for page, through_step_id, state in (
                (first, DESKEW_STEP_ID, StageState.FRESH),
                (second, DESKEW_STEP_ID, StageState.STALE),
                (whole, None, StageState.FRESH),
                (failed, DESKEW_STEP_ID, StageState.FAILED),
                (placeholder, DESKEW_STEP_ID, StageState.FRESH),
            )
        ]
        elsewhere_stopped = evolve(make_page_stage(page_id=elsewhere.id), through_step_id=DESKEW_STEP_ID)
        await fx_databases().open(book_id).change(partial(_save_records, records=stopped))
        await (
            fx_databases().open(other_id).change(lambda repos, _book_record: repos.page_stages.save(elsewhere_stopped))
        )
        [tally], steps = (
            await fx_databases()
            .open(book_id)
            .read(lambda repos: (repos.page_stages.tally(), repos.page_stages.step_tally()))
        )
        assert steps == [StepTally(stage=Stage.GEOMETRY, through_step_id=DESKEW_STEP_ID, pages=2)]
        assert tally.partial == 2

    async def test_deleting_the_head_version_leaves_the_record_without_it(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the record keeps its row when its head version is deleted.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        base = make_page_version(page_id=page_id)
        version = _version(base)

        def add_versions_and_record(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Add the versions and a record that names the version as its head.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            repos.page_versions.add_many([base, version])
            repos.page_stages.save(make_page_stage(page_id=page_id, head_version_id=version.id))

        await fx_databases().open(book_id).change(add_versions_and_record)
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.page_versions.delete(version.id))
        kept = (
            await fx_databases()
            .open(book_id)
            .read(lambda repos: repos.page_stages.get(PageStageKey(page_id, Stage.GEOMETRY)))
        )
        assert kept.head_version_id is None

    async def test_deleting_the_page_deletes_its_records_states_and_history(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the stage records, the step states and the history of the steps go with their page.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())

        def add_records(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Add a stage record, a step state with an edit and a change of the history of a step.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            repos.page_stages.save(make_page_stage(page_id=page_id))
            repos.page_step_states.save(make_page_step_state(page_id=page_id, edit=make_page_edit(page_id=page_id)))
            repos.page_history.add(make_page_history_entry(page_id=page_id))

        await fx_databases().open(book_id).change(add_records)
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.pages.delete(page_id))
        remaining = (
            await fx_databases()
            .open(book_id)
            .read(
                lambda repos: (
                    repos.page_stages.find(PageStageKey(page_id, Stage.GEOMETRY)),
                    repos.page_step_states.list_for_page(page_id),
                    repos.page_history.list_for_page(page_id),
                ),
            )
        )
        assert remaining == (None, [], [])


class TestPageStepStateRepository:
    """Tests for the settings and the manual edits of the steps of the pages."""

    async def test_save_stores_a_state_and_replaces_it(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a second save of the same step on the same page and stage replaces the state.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        first = make_page_step_state(page_id=page_id, params={MAX_ANGLE: 3})
        replacement = make_page_step_state(page_id=page_id, params={MAX_ANGLE: 7})

        def save_twice(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Save a state and then another one of the same step in its place.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            repos.page_step_states.save(first)
            repos.page_step_states.save(replacement)

        await fx_databases().open(book_id).change(save_twice)
        key = PageStepKey(page_id, Stage.GEOMETRY, DESKEW_STEP_ID)
        assert await fx_databases().open(book_id).read(lambda repos: repos.page_step_states.find(key)) == replacement

    async def test_settings_and_edit_survive_the_store_together(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the fields a page changes and its manual edit read back, and a state may hold either alone.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        both = make_page_step_state(
            page_id=page_id, params={MAX_ANGLE: 3, METHOD: PROJECTION}, edit=make_page_edit(page_id=page_id)
        )
        settings_only = make_page_step_state(page_id=page_id, stage=Stage.CLEANUP, params={'threshold': 0.5})
        edit_only = make_page_step_state(
            page_id=page_id, stage=Stage.LAYOUT, edit=make_page_edit(page_id=page_id, stage=Stage.LAYOUT)
        )
        states = (both, settings_only, edit_only)

        def save_all_states(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Save the three states.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            for state in states:
                repos.page_step_states.save(state)

        await fx_databases().open(book_id).change(save_all_states)
        read_back = (
            await fx_databases()
            .open(book_id)
            .read(lambda repos: [repos.page_step_states.get(state.key) for state in states])
        )
        assert read_back == [both, settings_only, edit_only]

    async def test_geometry_and_mask_survive_the_store(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the split line a user drew reads back as the same line in the edit of a state.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        geometry = Line(start=Point(x=1100.5, y=0), end=Point(x=1104, y=1561))
        edit = PageEdit(
            page_id=page_id,
            stage=Stage.PAGE_SPLIT,
            step_id=await _new_step(book_id, databases=fx_databases(), stage=Stage.PAGE_SPLIT),
            kind=geometry.editor,
            geometry=geometry,
            mask_key=None,
            edit_hash=PageEdit.hash_of(geometry, None),
            updated_at=EPOCH,
        )
        state = make_page_step_state(page_id=page_id, stage=Stage.PAGE_SPLIT, step_id=edit.step_id, edit=edit)
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.page_step_states.save(state))
        assert await fx_databases().open(book_id).read(lambda repos: repos.page_step_states.get(state.key)) == state

    async def test_list_for_page_filters_by_stage(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the states of one stage are listed apart from those of the page's other stages.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        geometry = make_page_step_state(page_id=page_id, params={MAX_ANGLE: 3})
        cleanup = make_page_step_state(page_id=page_id, stage=Stage.CLEANUP, params={MAX_ANGLE: 3})

        def save_both(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Save the state of the later stage first.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            repos.page_step_states.save(cleanup)
            repos.page_step_states.save(geometry)

        await fx_databases().open(book_id).change(save_both)
        listed = (
            await fx_databases()
            .open(book_id)
            .read(
                lambda repos: (
                    repos.page_step_states.list_for_page(page_id, Stage.GEOMETRY),
                    repos.page_step_states.list_for_page(page_id),
                ),
            )
        )
        assert listed == ([geometry], [geometry, cleanup])

    async def test_two_steps_of_one_processor_keep_two_states(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify states of two steps on one page and stage are stored apart and found each by its step.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        first = make_page_step_state(page_id=page_id, params={MAX_ANGLE: 1})
        other = await _new_step(book_id, databases=fx_databases())
        second = make_page_step_state(page_id=page_id, step_id=other, params={MAX_ANGLE: 2})

        def save_both(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Save the states of the two steps.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            repos.page_step_states.save(first)
            repos.page_step_states.save(second)

        await fx_databases().open(book_id).change(save_both)
        found = (
            await fx_databases()
            .open(book_id)
            .read(lambda repos: (repos.page_step_states.find(first.key), repos.page_step_states.find(second.key)))
        )
        assert found == (first, second)

    async def test_list_for_step_reads_one_step_over_several_pages(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the states of one step on the asked pages are listed, and those of other steps or pages are not.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        first_record, first_page = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        second_record, second_page = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        on_first = make_page_step_state(page_id=first_page, params={MAX_ANGLE: 1})
        on_second = make_page_step_state(page_id=second_page, params={MAX_ANGLE: 2})
        other = await _new_step(first_record, databases=fx_databases())
        other_step = make_page_step_state(page_id=first_page, step_id=other, params={MAX_ANGLE: 3})

        def save_first_book(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Save the two states of the first book.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            repos.page_step_states.save(on_first)
            repos.page_step_states.save(other_step)

        await fx_databases().open(first_record).change(save_first_book)
        await (
            fx_databases()
            .open(second_record)
            .change(lambda repos, _book_record: repos.page_step_states.save(on_second))
        )
        listed, both, wrong_stage = (
            await fx_databases()
            .open(first_record)
            .read(
                lambda repos: (
                    repos.page_step_states.list_for_step([first_page], Stage.GEOMETRY, DESKEW_STEP_ID),
                    repos.page_step_states.list_for_step([first_page, second_page], Stage.GEOMETRY, DESKEW_STEP_ID),
                    repos.page_step_states.list_for_step([first_page], Stage.CLEANUP, DESKEW_STEP_ID),
                ),
            )
        )
        second_listed = (
            await fx_databases()
            .open(second_record)
            .read(
                lambda repos: repos.page_step_states.list_for_step([second_page], Stage.GEOMETRY, DESKEW_STEP_ID),
            )
        )
        assert (listed, both, wrong_stage, second_listed) == ([on_first], [on_first], [], [on_second])

    async def test_state_of_a_missing_page_is_not_found(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Reject a state whose page is not stored.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, _ = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        state = make_page_step_state(page_id=PageId(new_account_id()))
        with pytest.raises(NotFoundError):
            await fx_databases().open(book_id).change(lambda repos, _book_record: repos.page_step_states.save(state))

    async def test_delete_removes_a_state(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a deleted state is gone, and deleting it again is a NotFoundError.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        state = make_page_step_state(page_id=page_id, params={MAX_ANGLE: 3})

        def save_and_delete(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Save the state and delete it.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            repos.page_step_states.save(state)
            repos.page_step_states.delete(state.key)

        await fx_databases().open(book_id).change(save_and_delete)
        with pytest.raises(NotFoundError):
            await (
                fx_databases()
                .open(book_id)
                .change(lambda repos, _book_record: repos.page_step_states.delete(state.key))
            )


class TestStepValuesRepository:
    """Tests for the values of the steps for the odd pages, the even pages and the groups."""

    async def test_save_stores_values_and_replaces_them(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a second save for the same step and part of the pages replaces the values, and they read back.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, _ = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        first = make_step_values(params={MAX_ANGLE: 3})
        replacement = make_step_values(params={MAX_ANGLE: 7, METHOD: PROJECTION})

        def save_twice(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Save values and then other values for the same step and part of the pages.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            repos.step_values.save(first)
            repos.step_values.save(replacement)

        await fx_databases().open(book_id).change(save_twice)
        found = await fx_databases().open(book_id).read(lambda repos: repos.step_values.find(replacement.key))
        assert found == replacement

    async def test_every_part_of_the_pages_keeps_its_own_values_of_a_step(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the odd pages, the even pages and two groups are four rows, listed by scope and group label.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, _ = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        parts = [
            make_step_values(scope=ValueScope.GROUP, group_label=INDEX_GROUP),
            make_step_values(scope=ValueScope.EVEN, params={MAX_ANGLE: 4}),
            make_step_values(scope=ValueScope.ODD, params={MAX_ANGLE: 5}),
            make_step_values(scope=ValueScope.GROUP, group_label=APPENDIX_GROUP),
        ]
        await fx_databases().open(book_id).change(partial(_save_values, values=parts))
        listed = await fx_databases().open(book_id).read(lambda repos: repos.step_values.list_for_step(DESKEW_STEP_ID))
        assert [(values.scope, values.group_label) for values in listed] == [
            (ValueScope.GROUP, APPENDIX_GROUP),
            (ValueScope.GROUP, INDEX_GROUP),
            (ValueScope.ODD, ''),
            (ValueScope.EVEN, ''),
        ]

    async def test_lists_keep_the_steps_and_the_books_apart(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the values of another step and another book are not listed, and a book lists every stage.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, _ = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        other_book_id, _ = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        mine = make_step_values()
        other_step = make_step_values(step_id=await _new_step(book_id, databases=fx_databases()))
        cleanup_step = await _new_step(book_id, databases=fx_databases(), stage=Stage.CLEANUP)
        other_stage = evolve(make_step_values(step_id=cleanup_step), stage=Stage.CLEANUP)
        other_book = make_step_values(step_id=await _new_step(other_book_id, databases=fx_databases()))
        await fx_databases().open(book_id).change(partial(_save_values, values=[mine, other_step, other_stage]))
        await fx_databases().open(other_book_id).change(lambda repos, _book_record: repos.step_values.save(other_book))
        listed = (
            await fx_databases()
            .open(book_id)
            .read(
                lambda repos: (
                    repos.step_values.list_for_step(DESKEW_STEP_ID),
                    repos.step_values.list_all(),
                ),
            )
        )
        assert listed == (
            [mine],
            sorted([mine, other_step, other_stage], key=lambda values: str(values.step_id)),
        )

    async def test_two_books_sharing_a_step_keep_separate_values(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify books built from one profile, which share a step id, keep, replace and delete their values apart.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        first_id, _ = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        second_id, _ = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        first = make_step_values(params={MAX_ANGLE: 3})
        second = make_step_values(params={MAX_ANGLE: 9})
        await fx_databases().open(first_id).change(lambda repos, _book_record: repos.step_values.save(first))
        await fx_databases().open(second_id).change(lambda repos, _book_record: repos.step_values.save(second))

        async def values_of(book_id: BookId) -> tuple[Sequence[StepValues], StepValues | None, StepValues | None]:
            """Read the values of the step of one book, and both keys in it.

            :param book_id: The book.
            :type book_id: BookId
            :returns: The values of the step, then what each of the two keys finds in the book.
            :rtype: tuple[Sequence[StepValues], StepValues | None, StepValues | None]
            """
            return (
                await fx_databases()
                .open(book_id)
                .read(
                    lambda repos: (
                        repos.step_values.list_for_step(DESKEW_STEP_ID),
                        repos.step_values.find(first.key),
                        repos.step_values.find(second.key),
                    ),
                )
            )

        # The two books keep one key in two files, so each finds its own values under it
        before = [await values_of(book_id) for book_id in (first_id, second_id)]
        expect(before == [([first], first, first), ([second], second, second)])
        await fx_databases().open(first_id).change(lambda repos, _book_record: repos.step_values.delete(first.key))
        after = [await values_of(book_id) for book_id in (first_id, second_id)]
        expect(after == [([], None, None), ([second], second, second)])
        assert_expectations()

    async def test_delete_removes_one_part_and_a_missing_one_is_not_found(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a deleted part is gone, the other part stays, and deleting what is not stored is refused.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, _ = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        even = make_step_values()
        odd = make_step_values(scope=ValueScope.ODD)
        await fx_databases().open(book_id).change(partial(_save_values, values=[even, odd]))
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.step_values.delete(even.key))
        with pytest.raises(NotFoundError):
            await fx_databases().open(book_id).change(lambda repos, _book_record: repos.step_values.delete(even.key))
        found = (
            await fx_databases()
            .open(book_id)
            .read(lambda repos: (repos.step_values.find(even.key), repos.step_values.find(odd.key)))
        )
        assert found == (None, odd)

    async def test_values_of_a_book_that_is_not_stored_are_refused(self, fx_databases: BookDatabasesFactory) -> None:
        """Verify values need a stored book, whose file holds them and is deleted with them.

        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        values = make_step_values()
        with pytest.raises(NotFoundError):
            await fx_databases().open(new_book_id()).change(lambda repos, _book_record: repos.step_values.save(values))

    async def test_deleting_the_book_deletes_its_values(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the values of a book are removed with it and those of another book stay.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, _ = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        other_book_id, _ = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        kept = make_step_values(scope=ValueScope.ODD)
        mine = make_step_values()
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.step_values.save(mine))
        await fx_databases().open(other_book_id).change(lambda repos, _book_record: repos.step_values.save(kept))
        await fx_databases().delete_book(book_id)
        remaining = (
            await fx_databases().open(other_book_id).read(lambda repos: repos.step_values.list_for_step(DESKEW_STEP_ID))
        )
        assert remaining == [kept]
        with pytest.raises(NotFoundError):
            await fx_databases().open(book_id).read(lambda repos: repos.step_values.list_for_step(DESKEW_STEP_ID))


class TestPageHistoryRepository:
    """Tests for the history of the layers of the steps of the pages."""

    async def test_change_survives_the_store(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a change reads back with its layers before and after, its source and its batch.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        change = evolve(
            make_page_history_entry(page_id=page_id, before=None, after={METHOD: 'otsu'}),
            scope=ValueScope.GROUP,
            group_label=INDEX_GROUP,
            source=ChangeSource.CARRY_OVER,
            batch_id=ChangeBatchId(uuid4()),
            undoes=PageHistoryEntryId(uuid4()),
        )
        stored = await fx_databases().open(book_id).change(lambda repos, _book_record: repos.page_history.add(change))
        read_back = await fx_databases().open(book_id).read(lambda repos: repos.page_history.list_for_page(page_id))
        assert (read_back, stored) == ([stored], evolve(change, sequence=1))

    async def test_a_change_of_two_pages_is_one_change_each_page_keeps_until_its_own_history_goes(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a change of values reaching two pages lists in both, and clearing it on one leaves it on the other.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        other = make_page(order_key=OTHER_ORDER_KEY)
        values = make_step_values(scope=ValueScope.ODD)
        change = PageHistoryEntry.of_values(
            [page_id, other.id], values, evolve(values, params={METHOD: 'otsu'}), ChangeSource.USER
        )

        def record(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Store the other page and the change of both pages.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book.
            :type _book_record: BookRecord
            """
            repos.pages.add(other)
            repos.page_history.add_many(change)

        await fx_databases().open(book_id).change(record)
        cleared = (
            await fx_databases()
            .open(book_id)
            .change(lambda repos, _book_record: repos.page_history.delete_for_step(change[0].key))
        )
        mine, theirs = (
            await fx_databases()
            .open(book_id)
            .read(
                lambda repos: (repos.page_history.list_for_page(page_id), repos.page_history.list_for_page(other.id)),
            )
        )

        expect(change[0].id == change[1].id)
        expect(cleared == 1)
        expect(mine == [])
        expect([(entry.id, entry.page_id) for entry in theirs] == [(change[1].id, other.id)])
        assert_expectations()

    async def test_changes_made_at_the_same_instant_list_in_the_order_they_were_written(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the sequence, not the time or the identifier, orders the history, across transactions and a batch.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        written = [make_page_history_entry(page_id=page_id, created_at=EPOCH) for _ in range(6)]

        def add_first_two(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Add the first two changes one by one.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            for change in written[:2]:
                repos.page_history.add(change)

        def add_the_rest(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Add three changes at once and then the last one.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            repos.page_history.add_many(written[2:5])
            repos.page_history.add(written[5])

        await fx_databases().open(book_id).change(add_first_two)
        await fx_databases().open(book_id).change(add_the_rest)
        listed = await fx_databases().open(book_id).read(lambda repos: repos.page_history.list_for_page(page_id))
        assert ([change.id for change in listed], [change.sequence for change in listed]) == (
            [change.id for change in written],
            [1, 2, 3, 4, 5, 6],
        )

    async def test_list_for_page_is_oldest_first_and_filters_by_stage(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the history of a page is listed from the oldest change, and one stage apart from the others.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        later = make_page_history_entry(page_id=page_id, created_at=EPOCH + RECENT)
        earlier = make_page_history_entry(page_id=page_id, created_at=EPOCH)
        cleanup = make_page_history_entry(page_id=page_id, stage=Stage.CLEANUP, created_at=EPOCH + OLD)
        await (
            fx_databases()
            .open(book_id)
            .change(lambda repos, _book_record: repos.page_history.add_many([earlier, later, cleanup]))
        )
        listed, in_stage = (
            await fx_databases()
            .open(book_id)
            .read(
                lambda repos: (
                    repos.page_history.list_for_page(page_id),
                    repos.page_history.list_for_page(page_id, Stage.GEOMETRY),
                ),
            )
        )
        assert ([change.id for change in listed], [change.id for change in in_stage]) == (
            [earlier.id, later.id, cleanup.id],
            [earlier.id, later.id],
        )

    async def test_list_for_batch_lists_the_changes_of_the_batch_of_every_page(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a batch lists its changes across pages, by page and sequence, and leaves out the other changes.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, first_page = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        second_page = make_page(order_key=SECOND_ORDER_KEY)
        batch = ChangeBatchId(uuid4())
        members = [
            evolve(make_page_history_entry(page_id=page_id), batch_id=batch)
            for page_id in (first_page, second_page.id, first_page)
        ]
        outside = [
            make_page_history_entry(page_id=first_page),
            evolve(make_page_history_entry(page_id=first_page), batch_id=ChangeBatchId(uuid4())),
        ]

        def add_page_and_changes(repos: BookRepositories, _book_record: BookRecord) -> list[PageHistoryEntry]:
            """Add the second page and every change.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            :returns: The changes as stored, with their sequence.
            :rtype: list[PageHistoryEntry]
            """
            repos.pages.add(second_page)
            return list(repos.page_history.add_many([*members, *outside]))

        stored = await fx_databases().open(book_id).change(add_page_and_changes)
        listed = await fx_databases().open(book_id).read(lambda repos: repos.page_history.list_for_batch(batch))
        expected = sorted(stored[: len(members)], key=attrgetter(PAGE_ID, 'sequence'))
        assert listed == expected

    async def test_list_undoing_finds_the_undos_of_the_given_changes_only(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the changes that take back the given changes are listed, and a change that stands has none.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        taken, standing, other = (make_page_history_entry(page_id=page_id) for _ in range(3))
        undo = evolve(make_page_history_entry(page_id=page_id), source=ChangeSource.UNDO, undoes=taken.id)
        unrelated = evolve(make_page_history_entry(page_id=page_id), source=ChangeSource.UNDO, undoes=other.id)
        await (
            fx_databases()
            .open(book_id)
            .change(
                lambda repos, _book_record: repos.page_history.add_many([taken, standing, other, undo, unrelated]),
            )
        )
        found, nothing = (
            await fx_databases()
            .open(book_id)
            .read(
                lambda repos: (
                    repos.page_history.list_undoing([taken.id, standing.id]),
                    repos.page_history.list_undoing([]),
                ),
            )
        )
        assert ([change.id for change in found], nothing) == ([undo.id], [])

    async def test_delete_for_step_deletes_that_step_on_that_page_only(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify every change of one step on one page goes, whatever its source, and the rest of the history stays.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        other_page = make_page(order_key=SECOND_ORDER_KEY)
        doomed = [make_page_history_entry(page_id=page_id) for _ in range(3)]
        undo = evolve(make_page_history_entry(page_id=page_id), source=ChangeSource.UNDO, undoes=doomed[0].id)
        carried = evolve(
            make_page_history_entry(page_id=page_id), source=ChangeSource.CARRY_OVER, batch_id=ChangeBatchId(uuid4())
        )
        other_step = evolve(
            make_page_history_entry(page_id=page_id), step_id=await _new_step(book_id, databases=fx_databases())
        )
        other_stage = make_page_history_entry(page_id=page_id, stage=Stage.CLEANUP)
        elsewhere = make_page_history_entry(page_id=other_page.id)

        def add_page_and_changes(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Add the other page and every change.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            repos.pages.add(other_page)
            repos.page_history.add_many([*doomed, undo, carried, other_step, other_stage, elsewhere])

        await fx_databases().open(book_id).change(add_page_and_changes)
        step = PageStepKey(page_id, Stage.GEOMETRY, DESKEW_STEP_ID)
        deleted = (
            await fx_databases()
            .open(book_id)
            .change(lambda repos, _book_record: repos.page_history.delete_for_step(step))
        )
        left, left_elsewhere = (
            await fx_databases()
            .open(book_id)
            .read(
                lambda repos: (
                    repos.page_history.list_for_page(page_id),
                    repos.page_history.list_for_page(other_page.id),
                ),
            )
        )
        assert (deleted, [change.id for change in left], [change.id for change in left_elsewhere]) == (
            5,
            [other_step.id, other_stage.id],
            [elsewhere.id],
        )

    async def test_delete_for_step_of_a_step_with_no_history_deletes_nothing(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a step with no change on the page is a count of zero and not an error.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        step = PageStepKey(page_id, Stage.GEOMETRY, DESKEW_STEP_ID)
        deleted = (
            await fx_databases()
            .open(book_id)
            .change(lambda repos, _book_record: repos.page_history.delete_for_step(step))
        )
        assert deleted == 0

    async def test_a_change_added_after_delete_for_step_is_numbered_above_every_remaining_one(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the sequence of the page goes on above the highest one it still holds, whichever changes were deleted.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        other_step = await _new_step(book_id, databases=fx_databases())
        written = [
            make_page_history_entry(page_id=page_id),
            evolve(make_page_history_entry(page_id=page_id), step_id=other_step),
            make_page_history_entry(page_id=page_id),
        ]
        step = PageStepKey(page_id, Stage.GEOMETRY, DESKEW_STEP_ID)
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.page_history.add_many(written))

        def delete_step_and_add(repos: BookRepositories, _book_record: BookRecord) -> PageHistoryEntry:
            """Delete the changes of the first step and add another change.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            :returns: The added change as stored.
            :rtype: PageHistoryEntry
            """
            repos.page_history.delete_for_step(step)
            return repos.page_history.add(make_page_history_entry(page_id=page_id))

        added = await fx_databases().open(book_id).change(delete_step_and_add)
        listed = await fx_databases().open(book_id).read(lambda repos: repos.page_history.list_for_page(page_id))
        assert ([change.sequence for change in listed], added.sequence) == ([2, 3], 3)

    async def test_change_of_a_missing_page_is_not_found(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Reject a change whose page is not stored.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, _ = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        change = make_page_history_entry(page_id=PageId(new_account_id()))
        with pytest.raises(NotFoundError):
            await fx_databases().open(book_id).change(lambda repos, _book_record: repos.page_history.add(change))

    async def test_change_is_stored_once(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a change with an identifier that is stored already is a conflict.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        change = make_page_history_entry(page_id=page_id)
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.page_history.add(change))
        with pytest.raises(ConflictError):
            await fx_databases().open(book_id).change(lambda repos, _book_record: repos.page_history.add(change))


class TestPageVersionProcessing:
    """Tests for the queries a run and a collection make on page versions, and for the columns processing added."""

    async def test_scale_edit_and_pyramid_survive_the_store(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the scale, the edit hash, the state of the pyramid and the review mark of a version read back.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        base = make_page_version(page_id=page_id)
        version = evolve(
            _version(base, scale=VersionScale.PREVIEW, edit_hash='0123456789abcdef', tiles_ready=True),
            review=ReviewReason.LOW_CONFIDENCE,
        )
        await (
            fx_databases()
            .open(book_id)
            .change(lambda repos, _book_record: repos.page_versions.add_many([base, version]))
        )
        assert await fx_databases().open(book_id).read(lambda repos: repos.page_versions.get(version.id)) == version

    async def test_find_returns_a_version_by_its_identifier_or_none(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a repeated run finds the version of the earlier run, and a new one finds nothing.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        base = make_page_version(page_id=page_id)
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.page_versions.add(base))
        found = (
            await fx_databases()
            .open(book_id)
            .read(lambda repos: (repos.page_versions.find(base.id), repos.page_versions.find(MISSING_VERSION_ID)))
        )
        assert found == (base, None)

    async def test_list_by_ids_reads_the_versions_found_and_leaves_out_the_others(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify one read gives the stored versions among the identifiers, the earliest first.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        older = make_page_version(page_id=page_id, minutes=1)
        newer = make_page_version(page_id=page_id, minutes=2)
        unwanted = make_page_version(page_id=page_id, minutes=3)
        await (
            fx_databases()
            .open(book_id)
            .change(lambda repos, _book_record: repos.page_versions.add_many([newer, older, unwanted]))
        )
        found = (
            await fx_databases()
            .open(book_id)
            .read(lambda repos: repos.page_versions.list_by_ids([newer.id, older.id, MISSING_VERSION_ID]))
        )
        assert [version.id for version in found] == [older.id, newer.id]

    async def test_list_for_stage_filters_and_pages_the_versions_of_a_page(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the versions of a page are filtered by stage and scale and cut into windows with their total.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        base = make_page_version(page_id=page_id)
        first = evolve(_version(base), created_at=EPOCH + timedelta(minutes=1))
        second = evolve(_version(base), created_at=EPOCH + timedelta(minutes=2))
        preview = evolve(_version(base, scale=VersionScale.PREVIEW), created_at=EPOCH + timedelta(minutes=3))
        cleanup = evolve(_version(base, stage=Stage.CLEANUP), created_at=EPOCH + timedelta(minutes=4))
        await (
            fx_databases()
            .open(book_id)
            .change(
                lambda repos, _book_record: repos.page_versions.add_many([base, first, second, preview, cleanup]),
            )
        )
        full_geometry, previews, window = (
            await fx_databases()
            .open(book_id)
            .read(
                lambda repos: (
                    repos.page_versions.list_for_stage(page_id, Stage.GEOMETRY, VersionScale.FULL, EVERYTHING),
                    repos.page_versions.list_for_stage(page_id, None, VersionScale.PREVIEW, EVERYTHING),
                    repos.page_versions.list_for_stage(page_id, Stage.GEOMETRY, None, SliceRequest(offset=1, limit=2)),
                ),
            )
        )
        assert (
            [version.id for version in full_geometry.items],
            [version.id for version in previews.items],
            ([version.id for version in window.items], window.total),
        ) == ([first.id, second.id], [preview.id], ([second.id, preview.id], 3))

    async def test_list_for_stage_filters_the_versions_by_mark(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a mark narrows the versions to those that carry it, and no mark lists marked and unmarked alike.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        base = make_page_version(page_id=page_id)
        bad = evolve(_version(base), mark=ResultMark.BAD, created_at=EPOCH + timedelta(minutes=1))
        good = evolve(_version(base), mark=ResultMark.GOOD, created_at=EPOCH + timedelta(minutes=2))
        unmarked = evolve(_version(base), created_at=EPOCH + timedelta(minutes=3))
        await (
            fx_databases()
            .open(book_id)
            .change(lambda repos, _book_record: repos.page_versions.add_many([base, bad, good, unmarked]))
        )
        listed_bad, listed_good, listed_all = (
            await fx_databases()
            .open(book_id)
            .read(
                lambda repos: (
                    repos.page_versions.list_for_stage(page_id, Stage.GEOMETRY, None, EVERYTHING, ResultMark.BAD),
                    repos.page_versions.list_for_stage(page_id, None, VersionScale.FULL, EVERYTHING, ResultMark.GOOD),
                    repos.page_versions.list_for_stage(page_id, Stage.GEOMETRY, None, EVERYTHING),
                ),
            )
        )
        assert (
            [version.id for version in listed_bad.items],
            [version.id for version in listed_good.items],
            [version.id for version in listed_all.items],
        ) == ([bad.id], [good.id], [bad.id, good.id, unmarked.id])

    async def test_collectable_is_non_base_and_outside_the_current_chains(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a collection may delete versions that no page shows and no chain of a shown version needs.

        The base version stays, a head and the version it was computed from stay, and a young preview stays. A full
        run that nothing refers to goes however recent it is, and so does an old preview.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        base = evolve(make_page_version(page_id=page_id), created_at=EPOCH)
        feeder = _version(base)
        head = evolve(_version(base, stage=Stage.CLEANUP), input_id=feeder.id)
        orphan = _version(base)
        recent = evolve(_version(base), created_at=NOW - RECENT)
        old_preview = _version(base, scale=VersionScale.PREVIEW)
        young_preview = evolve(_version(base, scale=VersionScale.PREVIEW), created_at=NOW - timedelta(minutes=5))

        def add_versions_and_head(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Add the versions and the record of the cleanup stage that names the head.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            repos.page_versions.add_many([base, feeder, head, orphan, recent, old_preview, young_preview])
            repos.page_stages.save(make_page_stage(page_id=page_id, stage=Stage.CLEANUP, head_version_id=head.id))

        await fx_databases().open(book_id).change(add_versions_and_head)
        found = await fx_databases().open(book_id).read(lambda repos: repos.page_versions.collectable(PREVIEW_CUTOFF))
        assert {version.id for version in found} == {orphan.id, recent.id, old_preview.id}

    async def test_collectable_keeps_a_version_marked_good_or_commented_with_its_inputs(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a collection leaves a version the user marked Good or wrote on, and the inputs it reads.

        A version marked Bad with no comment is no different from an unjudged one, and goes.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        base = evolve(make_page_version(page_id=page_id), created_at=EPOCH)
        input_of_good = _version(base)
        good = evolve(_version(base), input_id=input_of_good.id, mark=ResultMark.GOOD)
        commented = evolve(_version(base), comment='The margins are right.')
        commented_bad = evolve(_version(base), mark=ResultMark.BAD, comment='Too dark.')
        plain_bad = evolve(_version(base), mark=ResultMark.BAD)
        unjudged = _version(base)
        versions = [base, input_of_good, good, commented, commented_bad, plain_bad, unjudged]
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.page_versions.add_many(versions))
        found = await fx_databases().open(book_id).read(lambda repos: repos.page_versions.collectable(PREVIEW_CUTOFF))
        assert {version.id for version in found} == {plain_bad.id, unjudged.id}

    async def test_collectable_keeps_the_inputs_of_every_version_that_stays(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify an input goes only with the versions that read it, and not from under one that stays.

        The current version of a stage reads an input, which the database would leave it without if the input were
        deleted, and the input of that input stays too. A chain of versions that nothing outside it reads goes whole.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        base = evolve(make_page_version(page_id=page_id), created_at=EPOCH)
        first = _version(base)
        second = evolve(_version(base), input_id=first.id)
        reader = evolve(_version(base, stage=Stage.CLEANUP), input_id=second.id)
        chain_start = _version(base)
        chain_end = evolve(_version(base), input_id=chain_start.id)

        def add_versions_and_reader(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Add the versions and the record of the cleanup stage that names the reader.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            repos.page_versions.add_many([base, first, second, reader, chain_start, chain_end])
            repos.page_stages.save(make_page_stage(page_id=page_id, stage=Stage.CLEANUP, head_version_id=reader.id))

        await fx_databases().open(book_id).change(add_versions_and_reader)
        found = await fx_databases().open(book_id).read(lambda repos: repos.page_versions.collectable(PREVIEW_CUTOFF))
        assert {version.id for version in found} == {chain_start.id, chain_end.id}

    async def test_delete_many_removes_the_versions_and_ignores_missing_ones(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify several versions go in one call, and an identifier that is not stored is no error.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, page_id = await _store_page(fx_catalogue, fx_new_owner, databases=fx_databases())
        base = make_page_version(page_id=page_id)
        doomed = [_version(base), _version(base)]
        ids = [*(version.id for version in doomed), MISSING_VERSION_ID]
        await (
            fx_databases()
            .open(book_id)
            .change(lambda repos, _book_record: repos.page_versions.add_many([base, *doomed]))
        )
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.page_versions.delete_many(ids))
        left = await fx_databases().open(book_id).read(lambda repos: repos.page_versions.list_for_page(page_id))
        assert [version.id for version in left] == [base.id]
