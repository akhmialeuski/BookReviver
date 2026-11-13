"""Tests for the use cases of one open book: the change of its description and settings, and its overview."""

import io
import sqlite3
import zipfile
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.core.book import Book
from bookreviver.domain.changes import BookChanges, BookDetailsChanges
from bookreviver.domain.ids import BookId
from bookreviver.domain.images import ImagePolicy
from bookreviver.services.stage_summaries import StageSummaries
from tests.harness.adapters.database import database_file
from tests.harness.builders.domain import directory_of, make_page, make_record, make_scan, make_source
from tests.harness.builders.seeding import store_book
from tests.harness.fakes.processing import FakeCatalogue

if TYPE_CHECKING:
    from bookreviver.app.settings import Settings
    from bookreviver.domain.accounts import Actor
    from tests.harness.adapters.database import CatalogueDatabaseFactory
    from tests.harness.builders.domain import BookOpener

pytestmark = pytest.mark.anyio

PAGE_COUNT: int = 2
DATABASE_NAME: str = 'book.db'
OLD_TITLE: str = 'Old title'
NEW_TITLE: str = 'A title that only the book may keep'
NEW_PUBLISHER: str = 'A publisher that only the book may keep'


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

        overview = await _stages().overview(fx_open_book(fx_actor, book_id))

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

        assert (await _stages().overview(book)).book_record.image_policy == ImagePolicy.LOSSLESS

    async def test_the_description_is_read_back_from_the_book_and_stored_nowhere_in_the_catalogue(
        self,
        fx_catalogue: CatalogueDatabaseFactory,
        fx_actor: Actor,
        fx_open_book: BookOpener,
        fx_settings: Settings,
    ) -> None:
        """Verify a changed description is read back by the same book, and no table of ``catalogue.db`` holds it.

        :param fx_catalogue: Function opening a new catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_actor: Account that owns the book.
        :type fx_actor: Actor
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        :param fx_settings: Settings naming the file of the catalogue.
        :type fx_settings: Settings
        """
        book_id = await store_book(
            fx_catalogue(), make_record(title=OLD_TITLE), owner=fx_actor.account_id, databases=fx_open_book.databases
        )
        book = fx_open_book(fx_actor, book_id)

        await book.update(BookChanges(details=BookDetailsChanges(title=NEW_TITLE, publisher=NEW_PUBLISHER)))

        details = (await book.read(lambda repos: repos.book_record.get())).details
        with sqlite3.connect(database_file(fx_settings)) as catalogue:
            catalogue_text = '\n'.join(catalogue.iterdump())
        expect((details.title, details.publisher) == (NEW_TITLE, NEW_PUBLISHER))
        expect(NEW_TITLE not in catalogue_text and NEW_PUBLISHER not in catalogue_text)
        expect(str(book_id) in catalogue_text)
        assert_expectations()


class TestExport:
    """Tests for Book.export()."""

    async def test_the_database_is_copied_before_the_files_are_read_and_comes_first_in_the_archive(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_actor: Actor, fx_open_book: BookOpener
    ) -> None:
        """Verify a change made after ``export()`` returns, before the archive is read, is not in the archive.

        The files are read as the archive is read, so a copy of the database that already holds the change would have
        been taken after the reading began.

        :param fx_catalogue: Function opening a new catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_actor: Account that owns the book.
        :type fx_actor: Actor
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        book_id = await store_book(
            fx_catalogue(), make_record(title=OLD_TITLE), owner=fx_actor.account_id, databases=fx_open_book.databases
        )
        book = fx_open_book(fx_actor, book_id)
        archived = await book.export()
        await book.update(BookChanges(details=BookDetailsChanges(title=NEW_TITLE)))

        archive = io.BytesIO(b''.join([piece async for piece in archived.pieces]))

        with zipfile.ZipFile(archive) as opened:
            expect(opened.namelist()[0] == DATABASE_NAME)
        archive.seek(0)
        copy = Book(directory_of(BookId(uuid4())))
        await copy.unpack(archive)
        expect(archived.title == OLD_TITLE)
        expect((await copy.read(lambda repos: repos.book_record.get())).details.title == OLD_TITLE)
        expect((await book.read(lambda repos: repos.book_record.get())).details.title == NEW_TITLE)
        assert_expectations()
