"""A book of two sources with scans and a page, stored in the database and the stores as an import would have left it."""

from typing import TYPE_CHECKING, NamedTuple
from uuid import uuid4

from attrs import evolve

from bookreviver.domain.enums import Rendition
from bookreviver.domain.ids import JobId
from bookreviver.domain.keys import BookKeys
from bookreviver.domain.values import Renditions
from tests.helpers.builders import make_page, make_page_version, make_record, make_scan, make_source, new_account_id
from tests.helpers.seeding import commit_record
from tests.helpers.storage import upload

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import Actor, BookRecord, Page, PageVersion, Scan, Source
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.storage import AssetStore, SourceStore

SCANS_PER_SOURCE: int = 3
IMAGE: bytes = b'jpeg'
MAX_UPLOAD_BYTES: int = 1024


class Book(NamedTuple):
    """A book with two sources, each with scans, and a page cut from a scan of the first source.

    :ivar book_record: The record of the book.
    :ivar first: The source that the tests delete.
    :ivar second: The source that has to stay.
    :ivar scans: The scans of the first source, then those of the second, each with its renditions ready.
    :ivar page: The page cut from the first scan of the first source.
    :ivar version: The base version of that page, with its renditions ready.
    """

    book_record: BookRecord
    first: Source
    second: Source
    scans: list[Scan]
    page: Page
    version: PageVersion


async def commit_book(
    database: SqlDatabase, uow: UnitOfWork, sources: SourceStore, assets: AssetStore, owner: Actor | None
) -> Book:
    """Commit and store a book of two sources with scans, and one page cut from a scan of the first source.

    Every source has its file in the source store, and every scan and the page have their full image in the asset store.

    :param database: Catalogue to commit into, which gets the account of the owner when it does not store it.
    :type database: SqlDatabase
    :param uow: Unit of work to store the book through, over the same catalogue.
    :type uow: UnitOfWork
    :param sources: Source store receiving the two sources.
    :type sources: SourceStore
    :param assets: Asset store receiving the renditions of the scans and the image of the page.
    :type assets: AssetStore
    :param owner: The actor owning the book, or None for an account that acts nowhere else.
    :type owner: Actor | None
    :returns: The stored book.
    :rtype: Book
    """
    book_record = make_record()
    # Each source holds the scans made for it below, which is the number a read of the source counts
    first = evolve(make_source(name='part1.pdf'), scan_count=SCANS_PER_SOURCE)
    second = evolve(make_source(name='part2.pdf', minutes=1), scan_count=SCANS_PER_SOURCE)
    ready = Renditions(ready=True)
    scans = [
        evolve(make_scan(source=source, number=number), renditions=ready)
        for source in (first, second)
        for number in range(SCANS_PER_SOURCE)
    ]
    page = make_page(scan=scans[0])
    version = evolve(make_page_version(page_id=page.id), renditions=ready)
    owner_id = new_account_id() if owner is None else owner.account_id
    await commit_record(
        database, uow, book_record, page, owner=owner_id, sources=[first, second], scans=scans, versions=[version]
    )
    keys = BookKeys(book_record.id)
    for source in (first, second):
        job_id = JobId(uuid4())
        await sources.stage(book_record.id, job_id, [upload(source.file_name)], max_bytes=MAX_UPLOAD_BYTES)
        await sources.promote(book_record.id, job_id, source.id, names=[source.file_name])
    images = [keys.scan_rendition(scan, Rendition.FULL_JPEG) for scan in scans]
    for key in [*images, keys.version_rendition(version, Rendition.FULL_JPEG)]:
        async with assets.writable(key) as path:
            path.write_bytes(IMAGE)
    return Book(book_record=book_record, first=first, second=second, scans=scans, page=page, version=version)


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
    keys = BookKeys(book.book_record.id)
    scan = next(scan for scan in book.scans if scan.source_id == source.id)
    return (
        (root / keys.source(source.id) / source.file_name).is_file(),
        (root / keys.scan_rendition(scan, Rendition.FULL_JPEG)).is_file(),
    )
