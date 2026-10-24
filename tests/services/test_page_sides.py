"""Tests for the stages that go stale when a change of the places of the pages turns a page over to the other side."""

from typing import TYPE_CHECKING, NamedTuple

import pytest
from attrs import evolve

from bookreviver.domain.enums import NewPageOrigin, PageKind, Side, Stage, StageState, ValueScope
from bookreviver.domain.values import NewPage, PageAnchor
from tests.helpers.builders import make_page, make_page_stage, make_record, make_step_values, open_for
from tests.helpers.seeding import store_book

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from bookreviver.domain.entities import Actor, BookRecord, Page
    from bookreviver.ports.persistence import BookDatabases, BookRepositories, UnitOfWork
    from bookreviver.services.pages import PageService
    from tests.helpers.database import BookDatabasesFactory, UnitOfWorkFactory

pytestmark = pytest.mark.anyio

PAGE_COUNT: int = 4
CHECKED_STAGES: tuple[Stage, ...] = (Stage.GEOMETRY, Stage.CLEANUP)
GROUP: str = 'Index'


class Book(NamedTuple):
    """A book of four pages that all went through the geometry and the cleanup stage.

    :ivar book_record: The record of the book.
    :ivar pages: The pages in book order.
    """

    book_record: BookRecord
    pages: list[Page]


class Reordering(NamedTuple):
    """A change of the places of the pages, and the pages of the four-page book it turns over to the other side.

    :ivar act: The use case that changes the places.
    :ivar turned: Indexes of the pages whose place changed by an odd number.
    """

    act: Callable[[PageService, Actor, Book, BookDatabases], Awaitable[None]]
    turned: set[int]


async def _move(service: PageService, actor: Actor, book: Book, databases: BookDatabases) -> None:
    """Put the first page after the third, so the second and the third move up by one place.

    :param service: The page service.
    :type service: PageService
    :param actor: Owner of the book.
    :type actor: Actor
    :param book: The book.
    :type book: Book
    :param databases: Opens the databases of the books of the test.
    :type databases: BookDatabases
    """
    await service.move(
        open_for(actor, book.book_record.id, databases=databases),
        book.pages[0].id,
        PageAnchor(page_id=book.pages[2].id, side=Side.AFTER),
    )


async def _move_group(service: PageService, actor: Actor, book: Book, databases: BookDatabases) -> None:
    """Put the second and the third page after the fourth, so they move down by one place.

    :param service: The page service.
    :type service: PageService
    :param actor: Owner of the book.
    :type actor: Actor
    :param book: The book.
    :type book: Book
    :param databases: Opens the databases of the books of the test.
    :type databases: BookDatabases
    """
    anchor = PageAnchor(page_id=book.pages[3].id, side=Side.AFTER)
    await service.move_group(
        open_for(actor, book.book_record.id, databases=databases), [book.pages[1].id, book.pages[2].id], anchor
    )


async def _delete(service: PageService, actor: Actor, book: Book, databases: BookDatabases) -> None:
    """Delete the first page, so every page after it moves up by one place.

    :param service: The page service.
    :type service: PageService
    :param actor: Owner of the book.
    :type actor: Actor
    :param book: The book.
    :type book: Book
    :param databases: Opens the databases of the books of the test.
    :type databases: BookDatabases
    """
    await service.delete(open_for(actor, book.book_record.id, databases=databases), book.pages[0].id)


async def _insert(service: PageService, actor: Actor, book: Book, databases: BookDatabases) -> None:
    """Add a placeholder before the second page, so every page from it on moves down by one place.

    :param service: The page service.
    :type service: PageService
    :param actor: Owner of the book.
    :type actor: Actor
    :param book: The book.
    :type book: Book
    :param databases: Opens the databases of the books of the test.
    :type databases: BookDatabases
    """
    new_page = NewPage(
        origin=NewPageOrigin.PLACEHOLDER,
        kind=PageKind.TEXT,
        anchor=PageAnchor(page_id=book.pages[1].id, side=Side.BEFORE),
    )
    await service.add(open_for(actor, book.book_record.id, databases=databases), new_page)


REORDERINGS: dict[str, Reordering] = {
    'move': Reordering(_move, {1, 2}),
    'move-group': Reordering(_move_group, {1, 2}),
    'delete': Reordering(_delete, {1, 2, 3}),
    'insert': Reordering(_insert, {1, 2, 3}),
}


async def _commit_book(
    uow: UnitOfWork, actor: Actor, valued: tuple[ValueScope, Stage] | None, *, databases: BookDatabases
) -> Book:
    """Commit a book of four pages with a fresh record of each checked stage, and one value for a step of a stage.

    :param uow: Unit of work to commit through, over a database that stores the account of the owner.
    :type uow: UnitOfWork
    :param actor: Owner of the book.
    :type actor: Actor
    :param valued: The part of the pages and the stage of the one value the book has for a step, or None for no value.
    :type valued: tuple[ValueScope, Stage] | None
    :param databases: Opens the databases of the books of the test.
    :type databases: BookDatabases
    :returns: The book.
    :rtype: Book
    """
    book_record = make_record()
    pages = [make_page(order_key=f'a{number}') for number in range(PAGE_COUNT)]
    await store_book(uow, book_record, *pages, owner=actor.account_id, databases=databases)

    def save_records(repos: BookRepositories, _book_record: BookRecord) -> None:
        """Save a record for every checked stage of every page, and the value of the part of the pages when given.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :param _book_record: The record of the book as the change finds it, which the work does not need.
        :type _book_record: BookRecord
        """
        for page in pages:
            for stage in CHECKED_STAGES:
                repos.page_stages.save(make_page_stage(page_id=page.id, stage=stage))
        if valued is not None:
            scope, stage = valued
            group_label = GROUP if scope is ValueScope.GROUP else ''
            values = make_step_values(scope=scope, group_label=group_label)
            repos.step_values.save(evolve(values, stage=stage))

    await databases.open(book_record.id).change(save_records)
    return Book(book_record=book_record, pages=pages)


async def _stale(book: Book, *, databases: BookDatabases) -> set[tuple[int, Stage]]:
    """Read which stages of which pages are stale.

    :param book: The book.
    :type book: Book
    :param databases: Opens the databases of the books of the test.
    :type databases: BookDatabases
    :returns: The index of the page in the original book order and the stage, of each stale record.
    :rtype: set[tuple[int, Stage]]
    """
    index = {page.id: place for place, page in enumerate(book.pages)}
    records = await databases.open(book.book_record.id).read(
        lambda repos: repos.page_stages.list_for_pages(list(index))
    )
    return {(index[record.page_id], record.stage) for record in records if record.state is StageState.STALE}


class TestPagesTurnedOver:
    """Tests for the pages whose odd or even side changes when pages are moved, added or deleted."""

    @pytest.mark.parametrize('reordering', REORDERINGS.values(), ids=REORDERINGS.keys())
    async def test_the_pages_that_turned_over_go_stale_where_the_odd_pages_have_a_value(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        reordering: Reordering,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify a page whose place changed by an odd number goes stale in the stage of the value, and no other does.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param reordering: The change of the places and the pages it turns over.
        :type reordering: Reordering
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book = await _commit_book(fx_uow(), fx_actor, (ValueScope.ODD, Stage.GEOMETRY), databases=fx_databases())
        await reordering.act(fx_service(), fx_actor, book, fx_databases())
        assert await _stale(book, databases=fx_databases()) == {(place, Stage.GEOMETRY) for place in reordering.turned}

    async def test_only_the_stages_that_have_a_value_for_a_side_go_stale(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify the stage the even pages have a value in is marked, and the stage that has none is left fresh.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book = await _commit_book(fx_uow(), fx_actor, (ValueScope.EVEN, Stage.CLEANUP), databases=fx_databases())
        await _move(fx_service(), fx_actor, book, databases=fx_databases())
        assert await _stale(book, databases=fx_databases()) == {(1, Stage.CLEANUP), (2, Stage.CLEANUP)}

    @pytest.mark.parametrize('valued', [None, (ValueScope.GROUP, Stage.GEOMETRY)], ids=['no-value', 'group-value'])
    async def test_a_book_with_no_value_for_a_side_marks_nothing(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        valued: tuple[ValueScope, Stage] | None,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify moving a page leaves every stage fresh when no value is for the odd or the even pages.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param valued: The one value the book has, or None.
        :type valued: tuple[ValueScope, Stage] | None
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book = await _commit_book(fx_uow(), fx_actor, valued, databases=fx_databases())
        await _move(fx_service(), fx_actor, book, databases=fx_databases())
        assert await _stale(book, databases=fx_databases()) == set()
