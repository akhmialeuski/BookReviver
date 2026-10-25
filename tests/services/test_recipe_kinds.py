"""Tests for the recipe each page is processed by: the recipe of its kind, in every stage that has recipes."""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.changes import PageChanges
from bookreviver.domain.enums import ContentType, PageKind, RecipeKind, Stage, StageState
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.values import PageStageKey, RecipeDraft, RecipeKey, SliceRequest, StageRun, Step
from bookreviver.services.recipes import DefaultRecipes, RecipeTemplate
from tests.helpers.builders import open_for
from tests.helpers.page_services import make_page_service
from tests.helpers.processing import ProcessingKit
from tests.helpers.processors import FakeProcessor
from tests.helpers.spreads import run_stage

if TYPE_CHECKING:
    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import Actor, BookRecord, PageStage, Recipe
    from bookreviver.domain.ids import BookId, PageId
    from bookreviver.ports.persistence import BookRepositories
    from tests.helpers.builders import BookOpener
    from tests.helpers.database import LocatedBookDatabases

pytestmark = pytest.mark.anyio

FAKE_KEY: str = FakeProcessor.spec.key
STRENGTH_PARAM: str = 'strength'
# The strength the step of the recipe of each kind runs with, by which the versions of one kind are told from another's
STRENGTHS: dict[RecipeKind, int] = {
    RecipeKind.TEXT: 2,
    RecipeKind.COLOR_PICTURE: 3,
    RecipeKind.BW_PICTURE: 4,
    RecipeKind.BLANK: 5,
}
RECIPE_STAGES: tuple[Stage, ...] = (Stage.PAGE_SPLIT, Stage.GEOMETRY, Stage.CLEANUP)
TEMPLATE_STRENGTH: int = 7


async def seed_book(kit: ProcessingKit, kinds: list[PageKind]) -> tuple[Actor, BookId, list[PageId]]:
    """Seed a book whose pages have the given kinds, each a scan page with the base version the stage reads.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param kinds: Kind of each page in book order.
    :type kinds: list[PageKind]
    :returns: The actor, the book and the identifiers of the pages in book order.
    :rtype: tuple[Actor, BookRecord, list[PageId]]
    """
    actor, book_id = await kit.seed_book()
    page_ids: list[PageId] = []
    for index, kind in enumerate(kinds):
        page_ids.append(await add_page(kit, book_id, kind, order_key=f'a{index}'))
    return actor, book_id, page_ids


async def add_page(kit: ProcessingKit, book_id: BookId, kind: PageKind, *, order_key: str) -> PageId:
    """Add a scan page of a kind to a book, with the base version the stage reads.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: Identifier of the book owning the page.
    :type book_id: BookId
    :param kind: Role of the page in the book.
    :type kind: PageKind
    :param order_key: Order key of the page.
    :type order_key: str
    :returns: The identifier of the page.
    :rtype: PageId
    """
    page, _ = await kit.seed_scan_page(book_id, order_key=order_key)
    await kit.seed_base_version(book_id, page)
    await kit.databases.open(book_id).change(lambda repos, _book_record: repos.pages.update(evolve(page, kind=kind)))
    return page.id


async def tune_recipes(kit: ProcessingKit, actor: Actor, book_id: BookId) -> dict[RecipeKind, Recipe]:
    """Give the step of the recipe of each kind of the geometry stage a strength of its own.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: Account owning the book.
    :type actor: Actor
    :param book_id: Identifier of the book whose recipes are changed.
    :type book_id: BookId
    :returns: The recipes as stored, by kind.
    :rtype: dict[RecipeKind, Recipe]
    """
    service = kit.service()
    tuned: dict[RecipeKind, Recipe] = {}
    for recipe in (
        await service.recipes(
            open_for(actor, book_id),
            Stage.GEOMETRY,
            SliceRequest(limit=10),
        )
    ).items:
        draft = RecipeDraft(steps=[Step(processor_key=FAKE_KEY, params={STRENGTH_PARAM: STRENGTHS[recipe.kind]})])
        tuned[recipe.kind] = await service.save_recipe(
            open_for(actor, book_id),
            RecipeKey(Stage.GEOMETRY, recipe.id),
            draft,
        )
    return tuned


async def run_all(kit: ProcessingKit, actor: Actor, book_id: BookId, run: StageRun | None = None) -> None:
    """Run the geometry stage over the pages of the book, and let the board run it.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: Account owning the book.
    :type actor: Actor
    :param book_id: Identifier of the book to run in.
    :type book_id: BookId
    :param run: What to run, or None for the whole book.
    :type run: StageRun | None
    """
    await run_stage(kit, actor, book_id, StageRun(stage=Stage.GEOMETRY) if run is None else run)


async def record_of(book_id: BookId, page_id: PageId, *, databases: LocatedBookDatabases) -> PageStage:
    """Read the record of the geometry stage of a page.

    :param book_id: Book that holds the page.
    :type book_id: BookId
    :param page_id: The page.
    :type page_id: PageId
    :param databases: Opens the databases of the books of the test.
    :type databases: LocatedBookDatabases
    :returns: The record as committed.
    :rtype: PageStage
    """
    return await databases.open(book_id).read(
        lambda repos: repos.page_stages.get(PageStageKey(page_id, Stage.GEOMETRY))
    )


class TestRecipesOfAStage:
    """Tests for the recipes a stage starts with."""

    @pytest.mark.parametrize('stage', RECIPE_STAGES)
    async def test_every_stage_has_one_recipe_for_each_kind(
        self, fx_kit: ProcessingKit, stage: Stage, fx_open_book: BookOpener
    ) -> None:
        """Verify the first time a stage is asked for it gets the four recipes, each with steps of its own.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param stage: Stage under test.
        :type stage: Stage
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id = await fx_kit.seed_book()
        found = (await fx_kit.service().recipes(fx_open_book(actor, book_id), stage, SliceRequest(limit=10))).items
        step_ids = [step.step_id for recipe in found for step in recipe.steps]
        expect([recipe.kind for recipe in found] == list(RecipeKind))
        expect(len({recipe.id for recipe in found}) == len(RecipeKind))
        expect(len(set(step_ids)) == len(step_ids))
        assert_expectations()

    async def test_a_kind_without_a_template_starts_with_a_copy_of_the_recipe_of_text(
        self,
        fx_database: SqlDatabase,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify a template that is given for text pages alone leaves the other kinds the same steps, identifiers too.

        :param fx_database: Database of the test.
        :type fx_database: SqlDatabase
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        template = RecipeTemplate(processor_keys=(FAKE_KEY,), params={FAKE_KEY: {STRENGTH_PARAM: TEMPLATE_STRENGTH}})
        kit = ProcessingKit(
            fx_database,
            fx_open_book,
            defaults=DefaultRecipes({Stage.GEOMETRY: {RecipeKind.TEXT: template}}),
        )
        actor, book_id = await kit.seed_book()
        found = (
            await kit.service().recipes(fx_open_book(actor, book_id), Stage.GEOMETRY, SliceRequest(limit=10))
        ).items
        expect([recipe.kind for recipe in found] == list(RecipeKind))
        expect({step.params[STRENGTH_PARAM] for recipe in found for step in recipe.steps} == {TEMPLATE_STRENGTH})
        expect(len({step.step_id for recipe in found for step in recipe.steps}) == 1)
        assert_expectations()


class TestRecipeOfAPage:
    """Tests for a run, which processes each page by the recipe of its kind."""

    async def test_a_page_is_processed_by_the_recipe_of_its_kind(self, fx_kit: ProcessingKit) -> None:
        """Verify text, a plate, a blank page and a picture found on a text page each go to the recipe of their kind.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_id, (text, plate, blank, found) = await seed_book(
            fx_kit, [PageKind.TEXT, PageKind.PLATE, PageKind.BLANK, PageKind.TEXT]
        )
        await fx_kit.databases.open(book_id).change(
            lambda repos, _book_record: repos.pages.update(
                evolve(repos.pages.get(found), content_type=ContentType.BW_PICTURE)
            ),
        )
        recipes = await tune_recipes(fx_kit, actor, book_id)

        await run_all(fx_kit, actor, book_id)

        chosen = [
            (await record_of(book_id, page_id, databases=fx_kit.databases)).recipe_id
            for page_id in (text, plate, blank, found)
        ]
        assert chosen == [
            recipes[RecipeKind.TEXT].id,
            recipes[RecipeKind.COLOR_PICTURE].id,
            recipes[RecipeKind.BLANK].id,
            recipes[RecipeKind.BW_PICTURE].id,
        ]

    async def test_what_the_user_set_decides_the_kind_of_a_page(self, fx_kit: ProcessingKit) -> None:
        """Verify a text page the user made a picture goes to the picture recipe and a plate made text to the text one.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_id, (by_hand, plate_as_text) = await seed_book(fx_kit, [PageKind.TEXT, PageKind.PLATE])

        def set_by_hand(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Give the first page a picture and the second text as their content, both set by hand.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not use.
            :type _book_record: BookRecord
            """
            for page_id, content in ((by_hand, ContentType.COLOR_PICTURE), (plate_as_text, ContentType.TEXT)):
                repos.pages.update(evolve(repos.pages.get(page_id), content_type=content, content_by_hand=True))

        await fx_kit.databases.open(book_id).change(set_by_hand)
        recipes = await tune_recipes(fx_kit, actor, book_id)

        await run_all(fx_kit, actor, book_id)

        chosen = [
            (await record_of(book_id, page_id, databases=fx_kit.databases)).recipe_id
            for page_id in (by_hand, plate_as_text)
        ]
        assert chosen == [recipes[RecipeKind.COLOR_PICTURE].id, recipes[RecipeKind.TEXT].id]

    async def test_a_run_of_some_pages_chooses_the_recipe_of_each(self, fx_kit: ProcessingKit) -> None:
        """Verify naming the pages of a run does not change the recipe each of them goes to.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_id, (_, plate) = await seed_book(fx_kit, [PageKind.TEXT, PageKind.PLATE])
        recipes = await tune_recipes(fx_kit, actor, book_id)
        await run_all(fx_kit, actor, book_id, StageRun(stage=Stage.GEOMETRY, page_ids=(plate,)))
        assert (await record_of(book_id, plate, databases=fx_kit.databases)).recipe_id == recipes[
            RecipeKind.COLOR_PICTURE
        ].id

    async def test_a_page_added_later_goes_to_the_recipe_of_its_kind(self, fx_kit: ProcessingKit) -> None:
        """Verify a plate that joins the book after the first run is processed by the recipe of plates on the next.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_id, _ = await seed_book(fx_kit, [PageKind.TEXT])
        recipes = await tune_recipes(fx_kit, actor, book_id)
        await run_all(fx_kit, actor, book_id)
        late_plate = await add_page(fx_kit, book_id, PageKind.PLATE, order_key='z0')
        await run_all(fx_kit, actor, book_id)
        assert (await record_of(book_id, late_plate, databases=fx_kit.databases)).recipe_id == recipes[
            RecipeKind.COLOR_PICTURE
        ].id

    async def test_changing_what_a_page_shows_moves_it_to_the_recipe_of_its_new_kind_and_marks_it_stale(
        self, fx_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify a page the user makes a black-and-white picture is stale, and the next run uses the picture recipe.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id, (page_id,) = await seed_book(fx_kit, [PageKind.TEXT])
        recipes = await tune_recipes(fx_kit, actor, book_id)
        await run_all(fx_kit, actor, book_id)
        before = await record_of(book_id, page_id, databases=fx_open_book.databases)
        pages = make_page_service(fx_kit.stores, (fx_kit.events, fx_kit.board))

        await pages.update(fx_open_book(actor, book_id), page_id, PageChanges(content_type=ContentType.BW_PICTURE))
        stale = await record_of(book_id, page_id, databases=fx_open_book.databases)
        await run_all(fx_kit, actor, book_id)
        after = await record_of(book_id, page_id, databases=fx_open_book.databases)

        expect((before.recipe_id, before.state) == (recipes[RecipeKind.TEXT].id, StageState.FRESH))
        expect((stale.recipe_id, stale.state) == (recipes[RecipeKind.TEXT].id, StageState.STALE))
        expect((after.recipe_id, after.state) == (recipes[RecipeKind.BW_PICTURE].id, StageState.FRESH))
        assert_expectations()

    async def test_marking_a_page_blank_moves_it_to_the_recipe_of_blank_pages_and_marks_it_stale(
        self, fx_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify the role of a page decides its kind too, so a page made blank is stale and runs by the blank recipe.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id, (page_id,) = await seed_book(fx_kit, [PageKind.TEXT])
        recipes = await tune_recipes(fx_kit, actor, book_id)
        await run_all(fx_kit, actor, book_id)
        pages = make_page_service(fx_kit.stores, (fx_kit.events, fx_kit.board))

        await pages.update(fx_open_book(actor, book_id), page_id, PageChanges(kind=PageKind.BLANK))
        stale = await record_of(book_id, page_id, databases=fx_open_book.databases)
        await run_all(fx_kit, actor, book_id)

        expect(stale.state is StageState.STALE)
        expect(
            (await record_of(book_id, page_id, databases=fx_open_book.databases)).recipe_id
            == recipes[RecipeKind.BLANK].id
        )
        assert_expectations()


class TestResetToTheDefaultSteps:
    """Tests for putting back the steps a stage starts with, which are the profile of the account or the template."""

    async def test_the_template_steps_come_back_and_the_recipe_keeps_what_is_its_own(
        self, fx_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify the steps are new ones of the template while the identifier and the kind of the recipe stay.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id, (plate,) = await seed_book(fx_kit, [PageKind.PLATE])
        recipes = await tune_recipes(fx_kit, actor, book_id)
        soft = recipes[RecipeKind.COLOR_PICTURE]
        await run_all(fx_kit, actor, book_id)
        reset = await fx_kit.profiles().reset(fx_open_book(actor, book_id), RecipeKey(Stage.GEOMETRY, soft.id))
        record = await record_of(book_id, plate, databases=fx_open_book.databases)
        expect((reset.id, reset.kind) == (soft.id, RecipeKind.COLOR_PICTURE))
        expect(
            [(step.processor_key, step.params) for step in reset.steps] == [(FAKE_KEY, {'strength': 1, 'fail': False})]
        )
        expect(reset.steps[0].step_id != soft.steps[0].step_id)
        expect(reset.profile_id is None)
        expect((record.recipe_id, record.state) == (soft.id, StageState.STALE))
        assert_expectations()

    async def test_a_recipe_takes_the_template_of_its_own_kind(
        self,
        fx_database: SqlDatabase,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify each recipe gets the template of its kind.

        :param fx_database: Database of the test.
        :type fx_database: SqlDatabase
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        templates = DefaultRecipes(
            {
                Stage.GEOMETRY: {
                    kind: RecipeTemplate(processor_keys=(FAKE_KEY,), params={FAKE_KEY: {STRENGTH_PARAM: strength}})
                    for kind, strength in STRENGTHS.items()
                }
            }
        )
        kit = ProcessingKit(fx_database, fx_open_book, defaults=templates)
        actor, book_id = await kit.seed_book()
        found = (
            await kit.service().recipes(fx_open_book(actor, book_id), Stage.GEOMETRY, SliceRequest(limit=10))
        ).items
        strengths = {}
        for recipe in found:
            await kit.service().save_recipe(
                fx_open_book(actor, book_id),
                RecipeKey(Stage.GEOMETRY, recipe.id),
                RecipeDraft(steps=[Step(processor_key=FAKE_KEY, params={STRENGTH_PARAM: TEMPLATE_STRENGTH})]),
            )
            reset = await kit.profiles().reset(fx_open_book(actor, book_id), RecipeKey(Stage.GEOMETRY, recipe.id))
            strengths[recipe.kind] = reset.steps[0].params[STRENGTH_PARAM]
        assert strengths == STRENGTHS

    async def test_a_recipe_named_under_another_stage_is_not_found(
        self, fx_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify a recipe named under a stage it does not belong to is refused.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id = await fx_kit.seed_book()
        recipe = (
            await fx_kit.service().recipes(fx_open_book(actor, book_id), Stage.GEOMETRY, SliceRequest(limit=10))
        ).items[0]
        with pytest.raises(NotFoundError):
            await fx_kit.profiles().reset(fx_open_book(actor, book_id), RecipeKey(Stage.CLEANUP, recipe.id))
