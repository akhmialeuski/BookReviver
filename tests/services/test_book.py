"""Tests for the use cases of one open book: its overview and the change of its description and settings."""

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.changes import BookChanges, BookDetailsChanges
from bookreviver.domain.enums import ImagePolicy
from bookreviver.domain.values import BookDetails
from tests.helpers.builders import EPOCH, make_book_tools, make_page, make_record, make_scan, make_source
from tests.helpers.clock import FixedClock
from tests.helpers.seeding import store_book

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor
    from bookreviver.services.book import BookTools
    from tests.helpers.builders import BookOpener
    from tests.helpers.database import UnitOfWorkFactory

pytestmark = pytest.mark.anyio

# When the clock of the tools of the book stands, later than any record the builders make
CHANGED_AT = EPOCH + timedelta(days=1)
PAGE_COUNT: int = 2
NEW_TITLE: str = 'Renamed'


@pytest.fixture
def fx_tools() -> BookTools:
    """Return the tools of a book whose clock stands at ``CHANGED_AT``.

    :returns: The tools, with no processor and no running job.
    :rtype: BookTools
    """
    return evolve(make_book_tools(), clock=FixedClock(CHANGED_AT))


class TestOverview:
    """Tests for Book.overview()."""

    async def test_returns_the_book_with_the_counts_of_its_book(
        self, fx_uow: UnitOfWorkFactory, fx_actor: Actor, fx_open_book: BookOpener
    ) -> None:
        """Verify the overview counts the included pages, the sources and the scans of the book.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account that owns the book.
        :type fx_actor: Actor
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        book_record = make_record()
        source = make_source()
        scans = [make_scan(source=source, number=number) for number in range(PAGE_COUNT)]
        pages = [make_page(order_key=f'a{scan.number}', scan=scan) for scan in scans]
        # A duplicate scan kept out of the book is no page of it
        book_id = await store_book(
            fx_uow(),
            book_record,
            *pages[:-1],
            evolve(pages[-1], included=False),
            owner=fx_actor.account_id,
            sources=[source],
            scans=scans,
            databases=fx_open_book.databases,
        )

        overview = await fx_open_book(fx_actor, book_id).overview()

        assert (overview.book_record, overview.page_count, overview.source_count, overview.scan_count) == (
            book_record,
            PAGE_COUNT - 1,
            1,
            PAGE_COUNT,
        )


class TestUpdate:
    """Tests for Book.update()."""

    async def test_sets_the_image_policy(
        self, fx_uow: UnitOfWorkFactory, fx_actor: Actor, fx_open_book: BookOpener
    ) -> None:
        """Verify the image policy of the book changes.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account that owns the book.
        :type fx_actor: Actor
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        book_id = await store_book(fx_uow(), make_record(), owner=fx_actor.account_id, databases=fx_open_book.databases)

        changed = await fx_open_book(fx_actor, book_id).update(BookChanges(image_policy=ImagePolicy.LOSSLESS))

        assert changed.book_record.image_policy == ImagePolicy.LOSSLESS

    async def test_changes_given_fields_and_touches_the_book(
        self, fx_uow: UnitOfWorkFactory, fx_actor: Actor, fx_open_book: BookOpener, fx_tools: BookTools
    ) -> None:
        """Verify the given fields change, the others stay, and only the update time moves, to the clock of the tools.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account that owns the book.
        :type fx_actor: Actor
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        :param fx_tools: Tools of the book, whose clock stands at ``CHANGED_AT``.
        :type fx_tools: BookTools
        """
        book_record = make_record(title='Old')
        book_id = await store_book(fx_uow(), book_record, owner=fx_actor.account_id, databases=fx_open_book.databases)
        book = evolve(fx_open_book(fx_actor, book_id), tools=fx_tools)

        overview = await book.update(BookChanges(details=BookDetailsChanges(title=NEW_TITLE)))

        stored = await book.read(lambda repos: repos.book_record.get())
        expect(stored == overview.book_record)
        expect(stored.details == BookDetails(title=NEW_TITLE))
        expect((stored.created_at, stored.updated_at) == (book_record.created_at, CHANGED_AT))
        assert_expectations()
