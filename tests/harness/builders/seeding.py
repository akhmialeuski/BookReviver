"""Storing what earlier requests would have stored, so a test starts from a known committed state.

A test describes the book it needs as a ``BookLayout``, its sources with their scans and its pages with their kinds,
labels and base versions, and ``seed_book`` stores a book of that layout and returns what was stored.
"""

from typing import TYPE_CHECKING, Self, TypedDict, Unpack
from uuid import uuid4

from attrs import evolve, field, frozen

from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.adapters.persistence.sqlalchemy.accounts import AccountTable
from bookreviver.domain.ids import JobId
from bookreviver.domain.images import Rendition, Renditions
from bookreviver.domain.keys import BookKeys
from bookreviver.domain.labels import LabeledStrEnum
from bookreviver.domain.library import LibraryEntry
from bookreviver.domain.pages import PageKind
from bookreviver.domain.spans import BlockKind
from bookreviver.domain.versions import VersionState
from bookreviver.ports.persistence import DEFAULT_CHANGE_WAIT_SECONDS
from tests.harness.adapters.storage import upload
from tests.harness.builders.domain import (
    EPOCH,
    directory_of,
    make_page,
    make_page_version,
    make_record,
    make_scan,
    make_source,
    new_account_id,
    new_book_id,
)
from tests.harness.builders.page_services import make_base_versions

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.books import BookRecord
    from bookreviver.domain.ids import AccountId, BookId, StorageKey
    from bookreviver.domain.pages import ContentType, Page
    from bookreviver.domain.sources import Scan, Source
    from bookreviver.domain.versions import PageVersion
    from bookreviver.ports.persistence import BookRepositories, CatalogueDatabase
    from tests.harness.adapters.database import LocatedBookDatabases
    from tests.harness.adapters.storage import SharedBookDirectories

# Domain of the addresses of seeded accounts, reserved for examples by RFC 2606
ACCOUNT_DOMAIN: str = 'example.org'
# Largest upload the source store of a seeded book accepts for the file of a source
MAX_UPLOAD_BYTES: int = 1024


class BookContent(TypedDict, total=False):
    """What a seeded book holds besides its record and its pages, each part left out when a test needs none.

    :ivar sources: Sources of the book.
    :ivar scans: Scans of those sources.
    :ivar versions: Versions of the pages.
    """

    sources: Sequence[Source]
    scans: Sequence[Scan]
    versions: Sequence[PageVersion]


async def commit_account(
    database: SqlDatabase, *, account_id: AccountId | None = None, hashed_password: str = ''
) -> AccountId:
    """Commit a verified account, which the library entry of a book in the catalogue refers to.

    :param database: SQL database to commit into.
    :type database: SqlDatabase
    :param account_id: Identifier the account gets, for a test whose signed-in actor exists before the database does;
                       a new one when omitted.
    :type account_id: AccountId | None
    :param hashed_password: Password hash the account stores, for a test that signs in; empty, which matches no
                            password, when omitted.
    :type hashed_password: str
    :returns: Identifier of the account.
    :rtype: AccountId
    """
    if account_id is None:
        account_id = new_account_id()
    account = AccountTable(
        id=account_id,
        email=f'{account_id}@{ACCOUNT_DOMAIN}',
        hashed_password=hashed_password,
        is_active=True,
        is_verified=True,
    )
    await database.write(
        lambda session: session.add(account),
        kind=BlockKind.OTHER,
        book=None,
        wait_seconds=DEFAULT_CHANGE_WAIT_SECONDS,
    )
    return account_id


async def ensure_account(database: SqlDatabase, account_id: AccountId) -> None:
    """Commit the account unless the catalogue stores it already, as the library entry of a book needs it.

    The catalogue of a test stores the signed-in actor's account from the start, so a book of the actor needs
    nothing more, and a book of any other account needs the account committed first.

    :param database: Catalogue to commit into.
    :type database: SqlDatabase
    :param account_id: Identifier of the account that owns a book.
    :type account_id: AccountId
    """
    if await database.read(lambda session: session.get(AccountTable, account_id)) is None:
        await commit_account(database, account_id=account_id)


async def store_book(
    catalogue: CatalogueDatabase,
    book_record: BookRecord,
    *pages: Page,
    owner: AccountId,
    databases: LocatedBookDatabases,
    book_id: BookId | None = None,
    **content: Unpack[BookContent],
) -> BookId:
    """Store a new book with its record, then its sources, their scans, its pages and their versions, and list it.

    The store of the book is made first, then the content of the book is stored by ``Book.change``, and the library
    entry of the owner is added last, as the creation of a book does. The entry names an account, so the catalogue must
    store the owner.

    :param catalogue: Catalogue database to store through, over a catalogue that stores the account owning the book.
    :type catalogue: CatalogueDatabase
    :param book_record: Record of the book to store.
    :type book_record: BookRecord
    :param pages: Pages of the book, which may show the given scans.
    :type pages: Page
    :param owner: Account owning the book.
    :type owner: AccountId
    :param databases: Opens the database that stores the content.
    :type databases: LocatedBookDatabases
    :param book_id: Identifier the book gets, for a test that builds what refers to the book before storing it; a new
                    one when omitted.
    :type book_id: BookId | None
    :param content: The sources of the book, their scans and the versions of the pages, as far as the book has them.
    :type content: Unpack[BookContent]
    :returns: The identifier of the new book.
    :rtype: BookId
    """

    def store_content(repos: BookRepositories, _book_record: BookRecord) -> None:
        """Store the sources, scans, pages and versions of the book in the order their keys need.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :param _book_record: Record of the book, which the work does not need.
        :type _book_record: BookRecord
        """
        repos.sources.add_many(content.get('sources', ()))
        repos.scans.add_many(content.get('scans', ()))
        repos.pages.add_many(pages)
        repos.page_versions.add_many(content.get('versions', ()))

    if book_id is None:
        book_id = new_book_id()
    await databases.create_book(book_id, book_record)
    await databases.open(book_id).change(store_content)
    entry = LibraryEntry(
        book_id=book_id, owner_id=owner, directory=directory_of(book_id), added_at=book_record.created_at
    )
    await catalogue.change(lambda repos: repos.library.add(entry))
    return book_id


async def commit_record(
    database: SqlDatabase,
    catalogue: CatalogueDatabase,
    book_record: BookRecord,
    *pages: Page,
    owner: AccountId,
    databases: LocatedBookDatabases,
    **content: Unpack[BookContent],
) -> BookId:
    """Store a new book as ``store_book`` does, after committing its owner account when the catalogue lacks it.

    :param database: Catalogue to commit the owner account into.
    :type database: SqlDatabase
    :param catalogue: Catalogue database to store the book through, over the same catalogue.
    :type catalogue: CatalogueDatabase
    :param book_record: Record of the book to store.
    :type book_record: BookRecord
    :param pages: Pages of the book, which may show the given scans.
    :type pages: Page
    :param owner: Account owning the book, which is committed first unless the catalogue stores it.
    :type owner: AccountId
    :param databases: Opens the database that stores the content.
    :type databases: LocatedBookDatabases
    :param content: The sources of the book, their scans and the versions of the pages, as far as the book has them.
    :type content: Unpack[BookContent]
    :returns: The identifier of the new book.
    :rtype: BookId
    """
    await ensure_account(database, owner)
    return await store_book(catalogue, book_record, *pages, owner=owner, databases=databases, **content)


class BaseVersion(LabeledStrEnum):
    """The base version a page of a layout gets, each kind built as a different group of tests needs it."""

    RECORDED = 'recorded', 'A version as the builder makes it, with no rendition ready'
    RENDERED = 'rendered', 'A version whose renditions are ready'
    CUT = 'cut', 'The ready version the whole of the scan of the page gives, as the import cuts it'

    def of(self, page: Page, scan: Scan | None) -> PageVersion:
        """Build this kind of base version of a page.

        :param page: The page the version belongs to.
        :type page: Page
        :param scan: The scan the page shows, which a ``CUT`` version needs, or None for a placeholder.
        :type scan: Scan | None
        :returns: The version.
        :rtype: PageVersion
        """
        if self is BaseVersion.CUT:
            if scan is None:
                msg = 'a cut version needs the scan of its page'
                raise ValueError(msg)
            return make_base_versions().split_none(page=page, scan=scan, state=VersionState.READY, moment=EPOCH)
        version = make_page_version(page_id=page.id)
        if self is BaseVersion.RENDERED:
            return evolve(version, renditions=Renditions(ready=True))
        return version


@frozen
class ScanLayout:
    """What one scan of a source of a layout is like, beyond the grayscale scan the builder makes.

    :ivar size: Width and height in pixels and resolution of the scan, or None for the size of the builder.
    :ivar ready: Whether the renditions of the scan are ready.
    :ivar source_label: Label the source printed on the scan, or None for none.
    :ivar image: Content of the full image of the scan stored in the asset store, or None for no image.
    """

    size: tuple[int, int, float | None] | None = None
    ready: bool = False
    source_label: str | None = None
    image: bytes | None = None

    def build(self, source: Source, number: int) -> Scan:
        """Build the scan of a source.

        :param source: Source of the scan.
        :type source: Source
        :param number: Number of the scan within its source.
        :type number: int
        :returns: The scan.
        :rtype: Scan
        """
        scan = make_scan(source=source, number=number)
        if self.size is not None:
            width, height, dpi = self.size
            scan = evolve(scan, facts=evolve(scan.facts, width_px=width, height_px=height, dpi_x=dpi, dpi_y=dpi))
        if self.ready:
            scan = evolve(scan, renditions=Renditions(ready=True))
        if self.source_label is not None:
            scan = evolve(scan, source_label=self.source_label)
        return scan


@frozen
class SourceLayout:
    """One source of a layout and its scans.

    :ivar name: Name of the file of the source, which gives its digest.
    :ivar minutes: Minutes after the epoch the source was imported.
    :ivar scans: The scans of the source in their order.
    :ivar counted: Whether the source counts its scans, as a read of an imported source does; it counts none otherwise.
    :ivar uploaded: Whether the file of the source is in the source store.
    """

    name: str = 'book.pdf'
    minutes: int = 0
    scans: tuple[ScanLayout, ...] = ()
    counted: bool = False
    uploaded: bool = False

    @classmethod
    def of(
        cls,
        count: int,
        *,
        name: str = 'book.pdf',
        scan: ScanLayout | None = None,
        counted: bool = False,
        uploaded: bool = False,
    ) -> Self:
        """Describe a source of ``count`` scans all alike.

        :param count: Number of scans.
        :type count: int
        :param name: Name of the file of the source.
        :type name: str
        :param scan: What every scan is like, or None for the plain scan of the builder.
        :type scan: ScanLayout | None
        :param counted: Whether the source counts its scans.
        :type counted: bool
        :param uploaded: Whether the file of the source is in the source store.
        :type uploaded: bool
        :returns: The source.
        :rtype: Self
        """
        scans = (scan or ScanLayout(),) * count
        return cls(name=name, scans=scans, counted=counted, uploaded=uploaded)

    def build(self) -> tuple[Source, list[Scan]]:
        """Build the source and its scans.

        :returns: The source and its scans in their order.
        :rtype: tuple[Source, list[Scan]]
        """
        source = make_source(name=self.name, minutes=self.minutes)
        if self.counted:
            source = evolve(source, scan_count=len(self.scans))
        return source, [scan.build(source, number) for number, scan in enumerate(self.scans)]


@frozen
class PageLayout:
    """One page of a layout.

    :ivar kind: Role of the page in the book.
    :ivar included: Whether the page is in the book.
    :ivar scan: The index of the source in the layout and the number of the scan the page shows whole, or None for
                a placeholder.
    :ivar order_key: Order key of the page, or None for the one an even spread of the pages of the book gives.
    :ivar manual_label: Label the user gave the page by hand, or None for none.
    :ivar version: The base version the page has, or None for none.
    :ivar version_image: Content of the full image of the version stored in the asset store, or None for no image.
    :ivar notes: Notes of the user on the page, or None for the notes of the builder.
    :ivar content_type: What the detection found on the page, or None for what the builder gives.
    """

    kind: PageKind = PageKind.TEXT
    included: bool = True
    scan: tuple[int, int] | None = None
    order_key: str | None = None
    manual_label: str | None = None
    version: BaseVersion | None = None
    version_image: bytes | None = None
    notes: str | None = None
    content_type: ContentType | None = None

    def build(self, order_key: str, scan: Scan | None) -> Page:
        """Build the page.

        :param order_key: Order key the spread of the book gives the page, which its own key takes the place of.
        :type order_key: str
        :param scan: The scan the page shows, or None for a placeholder.
        :type scan: Scan | None
        :returns: The page.
        :rtype: Page
        """
        page = evolve(
            make_page(order_key=self.order_key or order_key, scan=scan),
            kind=self.kind,
            included=self.included,
            manual_label=self.manual_label,
        )
        if self.notes is not None:
            page = evolve(page, notes=self.notes)
        if self.content_type is not None:
            page = evolve(page, content_type=self.content_type)
        return page


@frozen
class BookParts:
    """The domain objects of a book of a layout, which a test stores as a whole.

    :ivar record: Record of the book.
    :ivar sources: Sources of the book in the order of the layout.
    :ivar scans: Scans of every source, the first source's first.
    :ivar pages: Pages of the book in the order of the layout, which is book order.
    :ivar versions: Base versions of the pages that have one, in the order of their pages.
    """

    record: BookRecord
    sources: list[Source]
    scans: list[Scan]
    pages: list[Page]
    versions: list[PageVersion]


@frozen
class SeededBook(BookParts):
    """A book of a layout as it is stored.

    :ivar book_id: Identifier of the book.
    """

    book_id: BookId = field(kw_only=True)


@frozen
class BookLayout:
    """What a book holds: its sources with their scans, and its pages with their kinds, labels and versions.

    :ivar sources: Sources of the book.
    :ivar pages: Pages of the book in book order.
    """

    sources: tuple[SourceLayout, ...] = ()
    pages: tuple[PageLayout, ...] = ()

    @classmethod
    def scanned(cls, *sources: SourceLayout, page: PageLayout | None = None) -> Self:
        """Describe a book with a page of each scan of its sources, in the order of the sources and their scans.

        :param sources: Sources of the book.
        :type sources: SourceLayout
        :param page: What every page is like but its scan, or None for an included text page.
        :type page: PageLayout | None
        :returns: The layout.
        :rtype: Self
        """
        alike = page or PageLayout()
        pages = tuple(
            evolve(alike, scan=(index, number))
            for index, source in enumerate(sources)
            for number in range(len(source.scans))
        )
        return cls(sources=sources, pages=pages)

    @classmethod
    def of_kinds(cls, *kinds: tuple[PageKind, bool], manual_label: str | None = None) -> Self:
        """Describe a book of placeholders of the given kinds and inclusion.

        :param kinds: Kind and inclusion of each page in book order.
        :type kinds: tuple[PageKind, bool]
        :param manual_label: Label every page gets by hand, or None for none.
        :type manual_label: str | None
        :returns: The layout.
        :rtype: Self
        """
        pages = tuple(PageLayout(kind=kind, included=included, manual_label=manual_label) for kind, included in kinds)
        return cls(pages=pages)

    @classmethod
    def of_placeholders(cls, count: int, *, manual_label: str | None = None) -> Self:
        """Describe a book of ``count`` included text placeholders.

        :param count: Number of pages.
        :type count: int
        :param manual_label: Label every page gets by hand, or None for none.
        :type manual_label: str | None
        :returns: The layout.
        :rtype: Self
        """
        return cls(pages=(PageLayout(manual_label=manual_label),) * count)

    def build(self) -> BookParts:
        """Build the record, the sources, the scans, the pages and the versions of a book of this layout.

        :returns: The objects of the book.
        :rtype: BookParts
        """
        built = [source.build() for source in self.sources]
        keys = FractionalOrderKeys().spread(lower=None, upper=None, count=len(self.pages))
        pages: list[Page] = []
        versions: list[PageVersion] = []
        for key, layout in zip(keys, self.pages, strict=True):
            scan = None if layout.scan is None else built[layout.scan[0]][1][layout.scan[1]]
            page = layout.build(key, scan)
            pages.append(page)
            if layout.version is not None:
                versions.append(layout.version.of(page, scan))
        return BookParts(
            record=make_record(),
            sources=[source for source, _ in built],
            scans=[scan for _, scans in built for scan in scans],
            pages=pages,
            versions=versions,
        )

    def images(self, book: BookParts) -> list[tuple[StorageKey, bytes]]:
        """List the keys and contents of the images a book of this layout stores in its asset store.

        :param book: The objects of the book built from this layout.
        :type book: BookParts
        :returns: The key and content of the full image of every scan and every version that has one.
        :rtype: list[tuple[StorageKey, bytes]]
        """
        scans = [scan for source in self.sources for scan in source.scans]
        images = [
            (BookKeys.scan_rendition(scan, Rendition.FULL_JPEG), layout.image)
            for scan, layout in zip(book.scans, scans, strict=True)
            if layout.image is not None
        ]
        versions = iter(book.versions)
        for layout in self.pages:
            if layout.version is None:
                continue
            version = next(versions)
            if layout.version_image is not None:
                images.append((BookKeys.version_rendition(version, Rendition.FULL_JPEG), layout.version_image))
        return images


async def seed_book(
    catalogue: CatalogueDatabase,
    layout: BookLayout,
    *,
    owner: AccountId,
    databases: LocatedBookDatabases,
    files: SharedBookDirectories | None = None,
) -> SeededBook:
    """Store a book of a layout, list it for its owner, and put the files the layout names in its stores.

    The library entry of the book names its owner, so the catalogue must store the account.

    :param catalogue: Catalogue database to store through.
    :type catalogue: CatalogueDatabase
    :param layout: What the book holds.
    :type layout: BookLayout
    :param owner: Account owning the book.
    :type owner: AccountId
    :param databases: Opens the database that stores the content.
    :type databases: LocatedBookDatabases
    :param files: Opens the stores of the book, which a layout with an uploaded source or an image needs.
    :type files: SharedBookDirectories | None
    :returns: The stored book.
    :rtype: SeededBook
    """
    parts = layout.build()
    book_id = await store_book(
        catalogue,
        parts.record,
        *parts.pages,
        owner=owner,
        databases=databases,
        sources=parts.sources,
        scans=parts.scans,
        versions=parts.versions,
    )
    uploads = [
        source for source, source_layout in zip(parts.sources, layout.sources, strict=True) if source_layout.uploaded
    ]
    images = layout.images(parts)
    if uploads or images:
        if files is None:
            msg = 'a layout with files needs the stores of the book'
            raise ValueError(msg)
        await _store_files(files, book_id, uploads, images)
    return SeededBook(
        record=parts.record,
        sources=parts.sources,
        scans=parts.scans,
        pages=parts.pages,
        versions=parts.versions,
        book_id=book_id,
    )


async def _store_files(
    files: SharedBookDirectories, book_id: BookId, uploads: list[Source], images: list[tuple[StorageKey, bytes]]
) -> None:
    """Put the files of the uploaded sources in the source store of a book and its images in its asset store.

    :param files: Opens the stores of the book.
    :type files: SharedBookDirectories
    :param book_id: The book.
    :type book_id: BookId
    :param uploads: Sources whose file the source store receives, as an import stages and promotes it.
    :type uploads: list[Source]
    :param images: Key and content of every image the asset store receives.
    :type images: list[tuple[StorageKey, bytes]]
    """
    sources, assets = files.sources(book_id), files.assets(book_id)
    for source in uploads:
        job_id = JobId(uuid4())
        await sources.stage(job_id, [upload(source.file_name)], max_bytes=MAX_UPLOAD_BYTES)
        await sources.promote(job_id, source.id, names=[source.file_name])
    for key, content in images:
        async with assets.writable(key) as path:
            path.write_bytes(content)
