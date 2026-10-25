"""Tests for the use cases of one open book: the change of its description and settings, and its overview."""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve

from bookreviver.domain.changes import BookChanges
from bookreviver.domain.enums import ImagePolicy
from bookreviver.services.stage_summaries import StageSummaries
from tests.helpers.builders import make_page, make_record, make_scan, make_source
from tests.helpers.fake_processing import FakeCatalogue
from tests.helpers.seeding import store_book

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor
    from tests.helpers.builders import BookOpener
    from tests.helpers.database import CatalogueDatabaseFactory

pytestmark = pytest.mark.anyio

PAGE_COUNT: int = 2


def _stages() -> StageSummaries:
    """Build the stage sums over a catalogue with no processors, for books whose queues hold no jobs.

    :returns: The stage summaries.
    :rtype: StageSummaries
    """
    return StageSummaries(processors=FakeCatalogue([]))


class TestOverview:
    """Tests for StageSummaries.overview() of a book."""

    async def test_returns_the_book_with_the_counts_of_its_book(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_actor: Actor, fx_open_book: BookOpener
    ) -> None:
        """Verify the overview counts the included pages, the sources and the scans of the book.

        :param fx_catalogue: Function opening a new catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
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
            fx_catalogue(),
            book_record,
            *pages[:-1],
            evolve(pages[-1], included=False),
            owner=fx_actor.account_id,
            sources=[source],
            scans=scans,
            databases=fx_open_book.databases,
        )

        overview = await _stages().overview(fx_open_book(fx_actor, book_id), book_id)

        assert (overview.book_record, overview.page_count, overview.source_count, overview.scan_count) == (
            book_record,
            PAGE_COUNT - 1,
            1,
            PAGE_COUNT,
        )


class TestUpdate:
    """Tests for Book.update()."""

    async def test_sets_the_image_policy(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_actor: Actor, fx_open_book: BookOpener
    ) -> None:
        """Verify the image policy of the book changes.

        :param fx_catalogue: Function opening a new catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_actor: Account that owns the book.
        :type fx_actor: Actor
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        book_id = await store_book(
            fx_catalogue(), make_record(), owner=fx_actor.account_id, databases=fx_open_book.databases
        )

        book = fx_open_book(fx_actor, book_id)

        await book.update(BookChanges(image_policy=ImagePolicy.LOSSLESS))

        assert (await _stages().overview(book, book_id)).book_record.image_policy == ImagePolicy.LOSSLESS
