"""Layout of storage keys, the one place that builds the key of any stored file of a book.

Every file of a book lives under ``projects/<book_id>/``, divided between the two storage ports:

- ``incoming/<job_id>/`` holds the upload of one import job while it is received and checked.
- ``sources/<source_id>/`` holds the files of one source exactly as uploaded.
- ``assets/scans/<source_id>/<number>/v<version>/`` holds the renditions of one scan in one version.
- ``assets/pages/<page_id>/<stage>/<processor>/<version_id>/`` holds one page version.
- ``assets/pages/<page_id>/edits/<step_id>/<edit_hash>/`` holds one manual edit of a page a step reads.
- ``assets/book/`` holds the results of whole-book steps, such as typesetting and export.

The ``SourceStore`` owns ``incoming/`` and ``sources/`` and the ``AssetStore`` owns ``assets/``, so each port removes
everything of a book it holds with one prefix. Keys name sources, scans and pages by identifier and never by
position, so moving a page keeps its keys. A key never holds an empty, ``.`` or ``..`` segment, so no key built here or
accepted by ``BookKeys.owning`` climbs out of its book.
"""

from typing import TYPE_CHECKING, ClassVar, Self
from uuid import UUID

from attrs import frozen

from bookreviver.domain.enums import LabeledStrEnum
from bookreviver.domain.ids import BookId, StorageKey

if TYPE_CHECKING:
    from bookreviver.domain.entities import PageVersion, Scan
    from bookreviver.domain.enums import Rendition
    from bookreviver.domain.ids import JobId, PageId, SourceId, StepId


class KeySegment(LabeledStrEnum):
    """A fixed directory name of the storage layout."""

    BOOKS = 'projects', 'Root of every book'
    INCOMING = 'incoming', 'Uploads being received, owned by the source store'
    SOURCES = 'sources', 'Files of the sources as uploaded, owned by the source store'
    ASSETS = 'assets', 'Derived files, owned by the asset store'
    SCANS = 'scans', 'Renditions of the scans'
    PAGES = 'pages', 'Versions and edits of the pages of the book'
    EDITS = 'edits', 'Manual edits of a page'
    BOOK = 'book', 'Results of whole-book steps'
    DATABASE = 'book.db', 'The database of the book, owned by the persistence adapter'


@frozen
class BookKeys:
    """The part of the storage key space that belongs to one book.

    :ivar book_id: Book whose keys are built.
    """

    book_id: BookId

    SEPARATOR: ClassVar[str] = '/'
    # Prefix of the directory of one version of a scan's renditions, as in ``v1``
    VERSION_PREFIX: ClassVar[str] = 'v'
    # A key of a derived file: the root, the book, ``assets`` and at least one name inside it
    MIN_SEGMENTS: ClassVar[int] = 4
    # Segments that would address the parent of a key or climb out of it
    UNSAFE_SEGMENTS: ClassVar[frozenset[str]] = frozenset({'', '.', '..'})
    # What the archive of a book carries: its database, its sources and its derived files, and no upload in progress
    ARCHIVED: ClassVar[frozenset[str]] = frozenset({KeySegment.DATABASE, KeySegment.SOURCES, KeySegment.ASSETS})

    @property
    def prefix(self) -> StorageKey:
        """The prefix shared by every key of the book, ending with the separator."""
        return StorageKey(f'{self._key()}{self.SEPARATOR}')

    @property
    def incoming_area(self) -> StorageKey:
        """The directory of every upload of the book being received."""
        return self._key(KeySegment.INCOMING)

    @property
    def sources_area(self) -> StorageKey:
        """The directory of every source of the book."""
        return self._key(KeySegment.SOURCES)

    @property
    def assets_area(self) -> StorageKey:
        """The directory of every derived file of the book."""
        return self._key(KeySegment.ASSETS)

    @property
    def database(self) -> StorageKey:
        """The file of the database of the book, beside the directories of its files."""
        return self._key(KeySegment.DATABASE)

    @property
    def book(self) -> StorageKey:
        """The directory of the results of whole-book steps."""
        return self._key(KeySegment.ASSETS, KeySegment.BOOK)

    def incoming(self, job_id: JobId) -> StorageKey:
        """Return the directory of the upload an import job receives.

        :param job_id: Import job owning the upload.
        :type job_id: JobId
        :returns: Key of ``incoming/<job_id>``.
        :rtype: StorageKey
        """
        return self._key(KeySegment.INCOMING, str(job_id))

    def source(self, source_id: SourceId) -> StorageKey:
        """Return the directory of the files of one source.

        :param source_id: Source whose files the directory holds.
        :type source_id: SourceId
        :returns: Key of ``sources/<source_id>``.
        :rtype: StorageKey
        """
        return self._key(KeySegment.SOURCES, str(source_id))

    def source_scans(self, source_id: SourceId) -> StorageKey:
        """Return the directory of the renditions of every scan of one source, removed with the source.

        :param source_id: Source whose scans the directory holds.
        :type source_id: SourceId
        :returns: Key of ``assets/scans/<source_id>``.
        :rtype: StorageKey
        """
        return self._key(KeySegment.ASSETS, KeySegment.SCANS, str(source_id))

    def scan_directory(self, scan: Scan) -> StorageKey:
        """Return the directory of every rendition of a scan in the scan's current version of renditions.

        :param scan: Scan of the book, whose source, number and renditions version place the directory.
        :type scan: Scan
        :returns: Key of ``assets/scans/<source_id>/<number>/v<version>``.
        :rtype: StorageKey
        """
        return self._key(
            KeySegment.ASSETS,
            KeySegment.SCANS,
            str(scan.source_id),
            str(scan.number),
            f'{self.VERSION_PREFIX}{scan.renditions.version}',
        )

    def scan_rendition(self, scan: Scan, rendition: Rendition) -> StorageKey:
        """Return the key of one rendition of a scan in the scan's current version of renditions.

        :param scan: Scan of the book, whose source, number and renditions version place the file.
        :type scan: Scan
        :param rendition: Derived file of the scan.
        :type rendition: Rendition
        :returns: Key of ``assets/scans/<source_id>/<number>/v<version>/<rendition>``.
        :rtype: StorageKey
        """
        return StorageKey(f'{self.scan_directory(scan)}{self.SEPARATOR}{rendition}')

    def page(self, page_id: PageId) -> StorageKey:
        """Return the directory of every version and edit of one page of the book, removed with the page.

        :param page_id: Page whose files the directory holds.
        :type page_id: PageId
        :returns: Key of ``assets/pages/<page_id>``.
        :rtype: StorageKey
        """
        return self._key(KeySegment.ASSETS, KeySegment.PAGES, str(page_id))

    def version_directory(self, version: PageVersion) -> StorageKey:
        """Return the directory of every rendition and output file of a page version.

        :param version: Page version, whose page, stage, processor and identifier place the directory.
        :type version: PageVersion
        :returns: Key of ``assets/pages/<page_id>/<stage>/<processor>/<version_id>``.
        :rtype: StorageKey
        :raises ValueError: If the processor key is not a safe directory name.
        """
        return self._key(
            KeySegment.ASSETS,
            KeySegment.PAGES,
            str(version.page_id),
            version.stage,
            version.processor.key,
            version.id,
        )

    def version_rendition(self, version: PageVersion, rendition: Rendition) -> StorageKey:
        """Return the key of one rendition of a page version, the version being a page of this book.

        :param version: Page version, whose page, stage, processor and identifier place the file.
        :type version: PageVersion
        :param rendition: Derived file of the version.
        :type rendition: Rendition
        :returns: Key of ``assets/pages/<page_id>/<stage>/<processor>/<version_id>/<rendition>``.
        :rtype: StorageKey
        :raises ValueError: If the processor key is not a safe directory name.
        """
        return StorageKey(f'{self.version_directory(version)}{self.SEPARATOR}{rendition}')

    def page_edits(self, page_id: PageId, step_id: StepId) -> StorageKey:
        """Return the directory of the manual edits of one page that one step reads.

        Masks stored before a step had an identifier lie in a directory named by the key of the processor, and the edit
        keeps the key of its mask, so those stay where they are.

        :param page_id: Page the edits belong to.
        :type page_id: PageId
        :param step_id: Step of a recipe reading the edits.
        :type step_id: StepId
        :returns: Key of ``assets/pages/<page_id>/edits/<step_id>``.
        :rtype: StorageKey
        """
        return self._key(KeySegment.ASSETS, KeySegment.PAGES, str(page_id), KeySegment.EDITS, str(step_id))

    def page_edit(self, page_id: PageId, step_id: StepId, edit_hash: str) -> StorageKey:
        """Return the directory of one manual edit, whose mask is stored there and never replaced by a later edit.

        :param page_id: Page the edit belongs to.
        :type page_id: PageId
        :param step_id: Step of a recipe reading the edit.
        :type step_id: StepId
        :param edit_hash: Hash of the edit, which gives each edit a directory of its own.
        :type edit_hash: str
        :returns: Key of ``assets/pages/<page_id>/edits/<step_id>/<hash>``.
        :rtype: StorageKey
        :raises ValueError: If the hash is not a safe directory name.
        """
        return StorageKey(f'{self.page_edits(page_id, step_id)}{self.SEPARATOR}{edit_hash}')

    @classmethod
    def owning(cls, key: StorageKey) -> Self | None:
        """Return the key space that a derived file at ``key`` belongs to, or None when it is not a well-formed one.

        Only keys under ``assets/`` are attributed, since those are the files the ``AssetStore`` serves. A key under
        ``incoming/`` or ``sources/`` belongs to the ``SourceStore`` and to no client, so it belongs to no one here.

        :param key: Key to attribute, such as one a client sent.
        :type key: StorageKey
        :returns: The keys of the book holding ``key``, or None for a key outside every book, naming a book
                  or its ``assets`` directory itself, lying outside ``assets``, or holding an empty, ``.`` or ``..``
                  segment.
        :rtype: Self | None
        """
        segments = key.split(cls.SEPARATOR)
        if (
            len(segments) < cls.MIN_SEGMENTS
            or segments[0] != KeySegment.BOOKS
            or segments[2] != KeySegment.ASSETS
            or cls.UNSAFE_SEGMENTS.intersection(segments)
        ):
            return None
        try:
            book_id = UUID(segments[1])
        except ValueError:
            return None
        return cls(BookId(book_id))

    def archived(self, name: str) -> StorageKey | None:
        """Return the key of a file of the archive of a book, which names it relative to the directory of the book.

        :param name: Path of the file in the archive, with ``/`` between its segments, such as ``assets/pages/...``.
        :type name: str
        :returns: The key of the file in this book, or None for a name outside what an archive carries: not the
                  database and not under ``sources/`` or ``assets/``, a directory of them alone, or a name holding an
                  empty, ``.`` or ``..`` segment.
        :rtype: StorageKey | None
        """
        segments = name.split(self.SEPARATOR)
        lone = segments[0] == KeySegment.DATABASE
        unsafe = self.UNSAFE_SEGMENTS.intersection(segments)
        if segments[0] not in self.ARCHIVED or lone != (len(segments) == 1) or unsafe:
            return None
        return self._key(*segments)

    def relative(self, key: StorageKey) -> str:
        """Return a key of this book relative to its directory, as an archive of the book names it.

        :param key: Key of a file of the book.
        :type key: StorageKey
        :returns: The key without the prefix of the book.
        :rtype: str
        """
        return key.removeprefix(self.prefix)

    def _key(self, *segments: str) -> StorageKey:
        """Join ``segments`` under the book's directory into a key.

        :param segments: Names below ``projects/<book_id>``, outermost first.
        :type segments: str
        :returns: The key of ``projects/<book_id>/<segments>``.
        :rtype: StorageKey
        :raises ValueError: If a segment is empty, ``.`` or ``..``, or holds the separator.
        """
        if unsafe := [segment for segment in segments if segment in self.UNSAFE_SEGMENTS or self.SEPARATOR in segment]:
            err_msg = f'Storage key segments may not be empty, "." or "..", or hold "{self.SEPARATOR}": {unsafe}.'
            raise ValueError(err_msg)
        return StorageKey(self.SEPARATOR.join((KeySegment.BOOKS, str(self.book_id), *segments)))
