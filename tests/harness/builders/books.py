"""A book of two sources with scans and a page, stored in the database and the stores as an import would have left it."""

from typing import TYPE_CHECKING, NamedTuple

from attrs import evolve

from bookreviver.domain.images import Rendition
from bookreviver.domain.keys import BookKeys
from tests.harness.adapters.storage import book_directory
from tests.harness.builders.domain import new_account_id
from tests.harness.builders.seeding import (
    BaseVersion,
    BookLayout,
    PageLayout,
    ScanLayout,
    SourceLayout,
    ensure_account,
    seed_book,
)

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.accounts import Actor
    from bookreviver.domain.ids import BookId
    from bookreviver.domain.pages import Page
    from bookreviver.domain.sources import Scan, Source
    from bookreviver.domain.versions import PageVersion
    from tests.harness.builders.domain import BookOpener

SCANS_PER_SOURCE: int = 3
IMAGE: bytes = b'jpeg'


class Book(NamedTuple):
    """A book with two sources, each with scans, and a page cut from a scan of the first source.

    :ivar book_id: The identifier of the book.
    :ivar first: The source that the tests delete.
    :ivar second: The source that has to stay.
    :ivar scans: The scans of the first source, then those of the second, each with its renditions ready.
    :ivar page: The page cut from the first scan of the first source.
    :ivar version: The base version of that page, with its renditions ready.
    """

    book_id: BookId
    first: Source
    second: Source
    scans: list[Scan]
    page: Page
    version: PageVersion


async def commit_book(database: SqlDatabase, stores: BookOpener, owner: Actor | None) -> Book:
    """Commit and store a book of two sources with scans, and one page cut from a scan of the first source.

    Every source has its file in the source store, and every scan and the page have their full image in the asset store.

    :param database: Catalogue to commit into, which gets the account of the owner when it does not store it.
    :type database: SqlDatabase
    :param stores: Opens the catalogue database to store the book through, over the same catalogue, the databases, and the
                   source store and the asset store receiving the sources, the renditions of the scans and the page.
    :type stores: BookOpener
    :param owner: The actor owning the book, or None for an account that acts nowhere else.
    :type owner: Actor | None
    :returns: The stored book.
    :rtype: Book
    """
    scan = ScanLayout(ready=True, image=IMAGE)
    sources = (
        SourceLayout.of(SCANS_PER_SOURCE, name='part1.pdf', scan=scan, counted=True, uploaded=True),
        evolve(SourceLayout.of(SCANS_PER_SOURCE, name='part2.pdf', scan=scan, counted=True, uploaded=True), minutes=1),
    )
    page = PageLayout(scan=(0, 0), version=BaseVersion.RENDERED, version_image=IMAGE)
    owner_id = new_account_id() if owner is None else owner.account_id
    await ensure_account(database, owner_id)
    book = await seed_book(
        stores.catalogue(),
        BookLayout(sources=sources, pages=(page,)),
        owner=owner_id,
        databases=stores.databases,
        files=stores.files,
    )
    first, second = book.sources
    return Book(
        book_id=book.book_id, first=first, second=second, scans=book.scans, page=book.pages[0], version=book.versions[0]
    )


def on_disk(root: Path, book: Book, source: Source) -> tuple[bool, bool]:
    """Report whether a source's file and its first scan's full image are on disk.

    :param root: Local storage root.
    :type root: Path
    :param book: The stored book.
    :type book: Book
    :param source: Source of the book whose files are checked.
    :type source: Source
    :returns: Whether the source file exists, and whether the full image of its first scan exists.
    :rtype: tuple[bool, bool]
    """
    scan = next(scan for scan in book.scans if scan.source_id == source.id)
    return (
        (book_directory(root, book.book_id) / BookKeys.source(source.id) / source.file_name).is_file(),
        (book_directory(root, book.book_id) / BookKeys.scan_rendition(scan, Rendition.FULL_JPEG)).is_file(),
    )
