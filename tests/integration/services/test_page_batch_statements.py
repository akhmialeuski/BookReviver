"""Tests for the SQL statements ``PageService.add_many`` sends to the book while it holds the writer of the book.

A batch of new pages reads the order of the book once for all its places and the pages it shows once for all its
pages, so the statements of the largest batch spread over many places are those of a batch of one page. A batch that
read the book for each place held the writer for seconds, and every other change of the book waited for it.
"""

from typing import TYPE_CHECKING

import pytest

from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.domain.pages import NewPage, NewPageOrigin, PageAnchor, PageKind
from bookreviver.domain.sides import Side
from tests.harness.adapters.statements import StatementLog
from tests.harness.builders.domain import directory_of, make_page, make_record, make_section, make_step_values
from tests.harness.builders.seeding import store_book

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from bookreviver.adapters.persistence.sqlalchemy.books import BookRegistry
    from bookreviver.domain.accounts import Actor
    from bookreviver.domain.books import BookRecord
    from bookreviver.domain.ids import BookId
    from bookreviver.domain.pages import Page
    from bookreviver.ports.persistence import BookRepositories
    from bookreviver.services.pages import PageService
    from tests.harness.builders.domain import BookOpener

pytestmark = pytest.mark.anyio

# Pages the book has before the batch, each of which may be the place of a run of the batch
BOOK_PAGES: int = 100
# Places of the large batch, each after another page of the book
MANY_PLACES: int = 90
# Pages of each place of the large batch, an odd number, so that pages of the book turn over to the other side and the
# side watch reads their records, as it does for the batch of one page
PAGES_PER_PLACE: int = 11
# Most statements a batch sends: the reads of the record, the sides, the sections and the order, the insert, the reads
# of what the new pages show, and the version of the book
STATEMENT_BOUND: int = 25


class BatchStatements:
    """Seeds books that number their pages and keep values for the even pages, and counts what a batch sends to them.

    :ivar open_book: Opens the books of the test with their databases.
    :ivar books: Registry of the books, whose engine of the writer of a book is listened to.
    :ivar actor: Account owning the books.
    :ivar service: Builds the page service for one request.
    """

    def __init__(
        self, open_book: BookOpener, books: BookRegistry, actor: Actor, service: Callable[[], PageService]
    ) -> None:
        """Count the statements of batches on the books of one test.

        :param open_book: Opens the books of the test with their databases.
        :type open_book: BookOpener
        :param books: Registry of the books, whose engine of the writer of a book is listened to.
        :type books: BookRegistry
        :param actor: Account owning the books.
        :type actor: Actor
        :param service: Builds the page service for one request.
        :type service: Callable[[], PageService]
        """
        self.open_book = open_book
        self.books = books
        self.actor = actor
        self.service = service

    async def sent(self, *, places: int, per_place: int) -> int:
        """Seed a book and count the statements of a batch of placeholders after its first pages.

        :param places: Number of places of the batch, the first of them after the first page of the book.
        :type places: int
        :param per_place: Number of placeholders in each place.
        :type per_place: int
        :returns: The number of statements the change of the batch sent.
        :rtype: int
        """
        book_id, pages = await self._seeded()
        new = [
            NewPage(
                origin=NewPageOrigin.PLACEHOLDER,
                kind=PageKind.TEXT,
                anchor=PageAnchor(page_id=pages[index // per_place].id, side=Side.AFTER)
                if index % per_place == 0
                else None,
            )
            for index in range(places * per_place)
        ]
        async with self.books.use(directory_of(book_id)) as database:
            with StatementLog(database.engine) as log:
                added = await self.service().add_many(self.open_book(self.actor, book_id), new)
        assert len(added) == len(new)
        return len(log.statements)

    async def _seeded(self) -> tuple[BookId, Sequence[Page]]:
        """Store a book of numbered pages with a section at its first page and values for its even pages.

        :returns: The identifier of the book and its pages in book order.
        :rtype: tuple[BookId, Sequence[Page]]
        """
        keys = FractionalOrderKeys().spread(lower=None, upper=None, count=BOOK_PAGES)
        pages = [make_page(order_key=key) for key in keys]
        book_id = await store_book(
            self.open_book.catalogue(),
            make_record(),
            *pages,
            owner=self.actor.account_id,
            databases=self.open_book.databases,
        )

        def numbered(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Start a section at the first page and give the even pages values for a step.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            repos.pagination_sections.add(make_section(page=pages[0]))
            repos.step_values.add(make_step_values())

        await self.open_book.databases.open(book_id).change(numbered)
        return book_id, pages


class TestAddManyStatements:
    """Tests for the number of statements a batch of new pages sends to its book."""

    async def test_a_thousand_pages_in_ninety_places_send_as_many_statements_as_one_page(
        self,
        fx_service: Callable[[], PageService],
        fx_open_book: BookOpener,
        fx_books: BookRegistry,
        fx_actor: Actor,
    ) -> None:
        """Verify the statements of a batch grow neither with its pages nor with its places, and stay few.

        The book has a section, so a read of the pages it shows numbers the whole book, and values for its even pages,
        so the order is watched for pages that turn over. Both batches send every read, the batch of one page included.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        :param fx_books: Registry of the books of the test, whose engines are listened to.
        :type fx_books: BookRegistry
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        batches = BatchStatements(fx_open_book, fx_books, fx_actor, fx_service)

        one = await batches.sent(places=1, per_place=1)
        many = await batches.sent(places=MANY_PLACES, per_place=PAGES_PER_PLACE)

        assert (many, one <= STATEMENT_BOUND) == (one, True)
