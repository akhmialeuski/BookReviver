"""Tests for the use cases of one open book: its overview and the change of its description and settings."""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve

from bookreviver.domain.changes import BookChanges
from bookreviver.domain.enums import ImagePolicy
from tests.helpers.builders import make_page, make_record, make_scan, make_source
from tests.helpers.seeding import store_book

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor
    from tests.helpers.builders import BookOpener
    from tests.helpers.database import UnitOfWorkFactory

pytestmark = pytest.mark.anyio

PAGE_COUNT: int = 2


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
