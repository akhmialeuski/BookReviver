"""A book of two sources with scans and a page, stored in the database and the stores as an import would have left it."""

from typing import TYPE_CHECKING, NamedTuple
from uuid import uuid4

from attrs import evolve

from bookreviver.domain.enums import Rendition
from bookreviver.domain.ids import JobId
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import Renditions
from tests.helpers.builders import make_page, make_page_version, make_project, make_scan, make_source, new_account_id
from tests.helpers.seeding import commit_project
from tests.helpers.storage import upload

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.domain.entities import Actor, Page, PageVersion, Project, Scan, Source
    from bookreviver.ports.storage import AssetStore, SourceStore

SCANS_PER_SOURCE: int = 3
IMAGE: bytes = b'jpeg'
MAX_UPLOAD_BYTES: int = 1024


class Book(NamedTuple):
    """A project with two sources, each with scans, and a page cut from a scan of the first source.

    :ivar project: The project.
    :ivar first: The source that the tests delete.
    :ivar second: The source that has to stay.
    :ivar scans: The scans of the first source, then those of the second, each with its renditions ready.
    :ivar page: The page cut from the first scan of the first source.
    :ivar version: The base version of that page, with its renditions ready.
    """

    project: Project
    first: Source
    second: Source
    scans: list[Scan]
    page: Page
    version: PageVersion


async def commit_book(
    database: InMemoryDatabase, sources: SourceStore, assets: AssetStore, owner: Actor | None
) -> Book:
    """Commit and store a project of two sources with scans, and one page cut from a scan of the first source.

    Every source has its file in the source store, and every scan and the page have their full image in the asset store.

    :param database: In-memory database to commit into.
    :type database: InMemoryDatabase
    :param sources: Source store receiving the two sources.
    :type sources: SourceStore
    :param assets: Asset store receiving the renditions of the scans and the image of the page.
    :type assets: AssetStore
    :param owner: The actor owning the project, or None for an account that acts nowhere else.
    :type owner: Actor | None
    :returns: The stored book.
    :rtype: Book
    """
    project = make_project(owner_id=new_account_id() if owner is None else owner.account_id)
    first = make_source(project_id=project.id, name='part1.pdf')
    second = make_source(project_id=project.id, name='part2.pdf', minutes=1)
    ready = Renditions(ready=True)
    scans = [
        evolve(make_scan(source=source, number=number), renditions=ready)
        for source in (first, second)
        for number in range(SCANS_PER_SOURCE)
    ]
    page = make_page(project_id=project.id, scan=scans[0])
    version = evolve(make_page_version(page_id=page.id), renditions=ready)
    await commit_project(database, project, page, sources=[first, second], scans=scans, versions=[version])
    keys = ProjectKeys(project.id)
    for source in (first, second):
        job_id = JobId(uuid4())
        await sources.stage(project.id, job_id, [upload(source.file_name)], max_bytes=MAX_UPLOAD_BYTES)
        await sources.promote(project.id, job_id, source.id, names=[source.file_name])
    images = [keys.scan_rendition(scan, Rendition.FULL_JPEG) for scan in scans]
    for key in [*images, keys.version_rendition(version, Rendition.FULL_JPEG)]:
        async with assets.writable(key) as path:
            path.write_bytes(IMAGE)
    return Book(project=project, first=first, second=second, scans=scans, page=page, version=version)


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
    keys = ProjectKeys(book.project.id)
    scan = next(scan for scan in book.scans if scan.source_id == source.id)
    return (
        (root / keys.source(source.id) / source.file_name).is_file(),
        (root / keys.scan_rendition(scan, Rendition.FULL_JPEG)).is_file(),
    )
