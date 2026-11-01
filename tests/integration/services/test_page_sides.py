"""Tests for the stages that go stale when a change of the places of the pages turns a page over to the other side."""

from typing import TYPE_CHECKING, NamedTuple

import pytest
from attrs import evolve

from bookreviver.domain.pages import NewPage, NewPageOrigin, PageAnchor, PageKind
from bookreviver.domain.sides import Side
from bookreviver.domain.stages import Stage, StageState
from bookreviver.domain.step_values import ValueScope
from tests.harness.builders.domain import make_page, make_page_stage, make_record, make_step_values
from tests.harness.builders.seeding import store_book

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from bookreviver.domain.accounts import Actor
    from bookreviver.domain.books import BookRecord
    from bookreviver.domain.ids import BookId
    from bookreviver.domain.pages import Page
    from bookreviver.ports.persistence import BookRepositories, CatalogueDatabase
    from bookreviver.services.pages import PageService
    from tests.harness.adapters.database import CatalogueDatabaseFactory, LocatedBookDatabases
    from tests.harness.builders.domain import BookOpener

pytestmark = pytest.mark.anyio

PAGE_COUNT: int = 4
CHECKED_STAGES: tuple[Stage, ...] = (Stage.GEOMETRY, Stage.CLEANUP)
GROUP: str = 'Index'


class Book(NamedTuple):
    """A book of four pages that all went through the geometry and the cleanup stage.

    :ivar book_id: Identifier of the book.
    :ivar pages: The pages in book order.
    """

    book_id: BookId
    pages: list[Page]


class Reordering(NamedTuple):
    """A change of the places of the pages, and the pages of the four-page book it turns over to the other side.

    :ivar act: The use case that changes the places.
    :ivar turned: Indexes of the pages whose place changed by an odd number.
    """

    act: Callable[[PageService, Actor, Book, BookOpener], Awaitable[None]]
    turned: set[int]


async def _move(service: PageService, actor: Actor, book: Book, opener: BookOpener) -> None:
    """Put the first page after the third, so the second and the third move up by one place.

    :param service: The page service.
    :type service: PageService
    :param actor: Owner of the book.
    :type actor: Actor
    :param book: The book.
    :type book: Book
    :param opener: Opens the books of the test with their databases and their files.
    :type opener: BookOpener
    """
    await service.move(
        opener(actor, book.book_id),
        book.pages[0].id,
        PageAnchor(page_id=book.pages[2].id, side=Side.AFTER),
    )


async def _move_group(service: PageService, actor: Actor, book: Book, opener: BookOpener) -> None:
    """Put the second and the third page after the fourth, so they move down by one place.

    :param service: The page service.
    :type service: PageService
    :param actor: Owner of the book.
    :type actor: Actor
    :param book: The book.
    :type book: Book
    :param opener: Opens the books of the test with their databases and their files.
    :type opener: BookOpener
    """
    anchor = PageAnchor(page_id=book.pages[3].id, side=Side.AFTER)
    await service.move_group(opener(actor, book.book_id), [book.pages[1].id, book.pages[2].id], anchor)


async def _delete(service: PageService, actor: Actor, book: Book, opener: BookOpener) -> None:
    """Delete the first page, so every page after it moves up by one place.

    :param service: The page service.
    :type service: PageService
    :param actor: Owner of the book.
    :type actor: Actor
    :param book: The book.
    :type book: Book
    :param opener: Opens the books of the test with their databases and their files.
    :type opener: BookOpener
    """
    await service.delete(opener(actor, book.book_id), book.pages[0].id)


async def _insert(service: PageService, actor: Actor, book: Book, opener: BookOpener) -> None:
    """Add a placeholder before the second page, so every page from it on moves down by one place.

    :param service: The page service.
    :type service: PageService
    :param actor: Owner of the book.
    :type actor: Actor
    :param book: The book.
    :type book: Book
    :param opener: Opens the books of the test with their databases and their files.
    :type opener: BookOpener
    """
    new_page = NewPage(
        origin=NewPageOrigin.PLACEHOLDER,
        kind=PageKind.TEXT,
        anchor=PageAnchor(page_id=book.pages[1].id, side=Side.BEFORE),
    )
    await service.add(opener(actor, book.book_id), new_page)


REORDERINGS: dict[str, Reordering] = {
    'move': Reordering(_move, {1, 2}),
    'move-group': Reordering(_move_group, {1, 2}),
    'delete': Reordering(_delete, {1, 2, 3}),
    'insert': Reordering(_insert, {1, 2, 3}),
}


async def _commit_book(
    catalogue: CatalogueDatabase,
    actor: Actor,
    valued: tuple[ValueScope, Stage] | None,
    *,
    databases: LocatedBookDatabases,
) -> Book:
    """Commit a book of four pages with a fresh record of each checked stage, and one value for a step of a stage.

    :param catalogue: Catalogue database to commit through, over a database that stores the account of the owner.
    :type catalogue: CatalogueDatabase
    :param actor: Owner of the book.
    :type actor: Actor
    :param valued: The part of the pages and the stage of the one value the book has for a step, or None for no value.
    :type valued: tuple[ValueScope, Stage] | None
    :param databases: Opens the databases of the books of the test.
    :type databases: LocatedBookDatabases
    :returns: The book.
    :rtype: Book
    """
    pages = [make_page(order_key=f'a{number}') for number in range(PAGE_COUNT)]
    book_id = await store_book(catalogue, make_record(), *pages, owner=actor.account_id, databases=databases)

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

    await databases.open(book_id).change(save_records)
    return Book(book_id=book_id, pages=pages)


async def _stale(book: Book, *, databases: LocatedBookDatabases) -> set[tuple[int, Stage]]:
    """Read which stages of which pages are stale.

    :param book: The book.
    :type book: Book
    :param databases: Opens the databases of the books of the test.
    :type databases: LocatedBookDatabases
    :returns: The index of the page in the original book order and the stage, of each stale record.
    :rtype: set[tuple[int, Stage]]
    """
    index = {page.id: place for place, page in enumerate(book.pages)}
    records = await databases.open(book.book_id).read(lambda repos: repos.page_stages.list_for_pages(list(index)))
    return {(index[record.page_id], record.stage) for record in records if record.state is StageState.STALE}


class TestPagesTurnedOver:
    """Tests for the pages whose odd or even side changes when pages are moved, added or deleted."""

    @pytest.mark.parametrize('reordering', REORDERINGS.values(), ids=REORDERINGS.keys())
    async def test_the_pages_that_turned_over_go_stale_where_the_odd_pages_have_a_value(
        self,
        fx_service: Callable[[], PageService],
        fx_catalogue: CatalogueDatabaseFactory,
        fx_actor: Actor,
        reordering: Reordering,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify a page whose place changed by an odd number goes stale in the stage of the value, and no other does.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param reordering: The change of the places and the pages it turns over.
        :type reordering: Reordering
        :param fx_open_book: Opens the books of the test with their databases and their files.
        :type fx_open_book: BookOpener
        """
        book = await _commit_book(
            fx_catalogue(), fx_actor, (ValueScope.ODD, Stage.GEOMETRY), databases=fx_open_book.databases
        )
        await reordering.act(fx_service(), fx_actor, book, fx_open_book)
        assert await _stale(book, databases=fx_open_book.databases) == {
            (place, Stage.GEOMETRY) for place in reordering.turned
        }

    async def test_only_the_stages_that_have_a_value_for_a_side_go_stale(
        self,
        fx_service: Callable[[], PageService],
        fx_catalogue: CatalogueDatabaseFactory,
        fx_actor: Actor,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify the stage the even pages have a value in is marked, and the stage that has none is left fresh.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_open_book: Opens the books of the test with their databases and their files.
        :type fx_open_book: BookOpener
        """
        book = await _commit_book(
            fx_catalogue(), fx_actor, (ValueScope.EVEN, Stage.CLEANUP), databases=fx_open_book.databases
        )
        await _move(fx_service(), fx_actor, book, opener=fx_open_book)
        assert await _stale(book, databases=fx_open_book.databases) == {(1, Stage.CLEANUP), (2, Stage.CLEANUP)}

    @pytest.mark.parametrize('valued', [None, (ValueScope.GROUP, Stage.GEOMETRY)], ids=['no-value', 'group-value'])
    async def test_a_book_with_no_value_for_a_side_marks_nothing(
        self,
        fx_service: Callable[[], PageService],
        fx_catalogue: CatalogueDatabaseFactory,
        fx_actor: Actor,
        valued: tuple[ValueScope, Stage] | None,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify moving a page leaves every stage fresh when no value is for the odd or the even pages.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param valued: The one value the book has, or None.
        :type valued: tuple[ValueScope, Stage] | None
        :param fx_open_book: Opens the books of the test with their databases and their files.
        :type fx_open_book: BookOpener
        """
        book = await _commit_book(fx_catalogue(), fx_actor, valued, databases=fx_open_book.databases)
        await _move(fx_service(), fx_actor, book, opener=fx_open_book)
        assert await _stale(book, databases=fx_open_book.databases) == set()
