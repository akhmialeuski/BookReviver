"""Tests for the choice of the recipe of each page over SQLite: it costs the same statements for any number of pages."""

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import event

from bookreviver.adapters.clock.system import FixedClock
from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from bookreviver.domain.enums import PageKind, RuleCondition, Stage
from bookreviver.domain.values import SliceRequest
from bookreviver.services.recipe_picks import RecipePicker
from bookreviver.services.recipes import DefaultRecipes, RecipeBook
from tests.helpers.builders import EPOCH, make_page, make_pinned_stage, make_project, make_recipe, make_recipe_rule
from tests.helpers.fake_processing import FakeCatalogue
from tests.helpers.statements import STATEMENT_EVENT, StatementCounter

if TYPE_CHECKING:
    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import Page
    from bookreviver.domain.ids import AccountId, ProjectId

pytestmark = pytest.mark.anyio

FEW_PAGES: int = 3
MANY_PAGES: int = 30
GROUP: str = 'Engravings'
# The kinds the pages of the book take in turn, so the rules of the stage match some pages and not others
KINDS: tuple[PageKind, ...] = (PageKind.TEXT, PageKind.PLATE, PageKind.COVER, PageKind.TEXT)
# A window that holds the whole book of the test
WHOLE_BOOK: SliceRequest = SliceRequest(limit=MANY_PAGES)


async def _commit_book(database: SqlDatabase, owner_id: AccountId) -> ProjectId:
    """Commit a book of many pages with a rule of every kind of test and a page pinned to a variant in every fourth.

    :param database: Fresh SQLite database with every table created.
    :type database: SqlDatabase
    :param owner_id: Committed account owning the book.
    :type owner_id: AccountId
    :returns: The identifier of the book.
    :rtype: ProjectId
    """
    async with database.sessions() as session:
        uow = SqlAlchemyUnitOfWork(session)
        project = await uow.projects.add(make_project(owner_id=owner_id))
        active = make_recipe(project_id=project.id, active=True)
        plates = make_recipe(project_id=project.id, name='Plates', minutes=1)
        soft = make_recipe(project_id=project.id, name='Soft', minutes=2)
        await uow.recipes.add_many([active, plates, soft])
        for order, (condition, label) in enumerate(
            [(RuleCondition.PLATES, ''), (RuleCondition.EVEN, ''), (RuleCondition.GROUP, GROUP)]
        ):
            await uow.recipe_rules.add(
                make_recipe_rule(recipe=plates, condition=condition, group_label=label, order=order)
            )
        for index in range(MANY_PAGES):
            page = await uow.pages.add(
                make_page(
                    project_id=project.id,
                    order_key=f'a{index:03d}',
                    kind=KINDS[index % len(KINDS)],
                    group_label=GROUP if index % 5 == 0 else '',
                )
            )
            if index % 4 == 0:
                await uow.page_stages.add(make_pinned_stage(page_id=page.id, recipe_id=soft.id))
        await uow.commit()
        return project.id


async def _statements_of_pick(database: SqlDatabase, project_id: ProjectId, count: int) -> int:
    """Choose the recipes of the first pages of the book and count the statements the choice sends.

    :param database: SQLite database holding the book.
    :type database: SqlDatabase
    :param project_id: Identifier of the book.
    :type project_id: ProjectId
    :param count: How many pages, from the first, the run goes over.
    :type count: int
    :returns: Number of statements sent while choosing.
    :rtype: int
    """
    async with database.sessions() as session:
        uow = SqlAlchemyUnitOfWork(session)
        book = RecipeBook(uow=uow, catalogue=FakeCatalogue([]), defaults=DefaultRecipes({}), clock=FixedClock(EPOCH))
        pages: list[Page] = list((await uow.pages.list_for_project(project_id, WHOLE_BOOK)).items)[:count]
        counter = StatementCounter()
        event.listen(database.engine.sync_engine, STATEMENT_EVENT, counter)
        try:
            picked = await RecipePicker(uow=uow, recipes=book).pick(project_id, Stage.GEOMETRY, pages)
        finally:
            event.remove(database.engine.sync_engine, STATEMENT_EVENT, counter)
    assert len(picked) == count
    return counter.count


class TestRecipePicksOverSqlite:
    """Tests for the statements ``RecipePicker.pick`` sends to SQLite."""

    async def test_choosing_for_many_pages_costs_the_statements_of_choosing_for_few(
        self, fx_database: SqlDatabase, fx_owner_id: AccountId
    ) -> None:
        """Verify thirty pages take as many statements as three, so no query runs per page.

        The rules include one on parity, which reads the order of the book, so the proof covers that read too.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the book.
        :type fx_owner_id: AccountId
        """
        project_id = await _commit_book(fx_database, fx_owner_id)
        few = await _statements_of_pick(fx_database, project_id, FEW_PAGES)
        many = await _statements_of_pick(fx_database, project_id, MANY_PAGES)
        # The counter saw the choice at work, so equal counts are not two zeros
        assert few > 0
        assert many == few
