"""Layout of storage keys, the one place that builds the key of any stored file of a project.

Every file of a project lives under ``projects/<project_id>/``, divided between the two storage ports:

- ``incoming/<job_id>/`` holds the upload of one import job while it is received and checked.
- ``sources/<source_id>/`` holds the files of one source exactly as uploaded.
- ``assets/scans/<source_id>/<number>/v<version>/`` holds the renditions of one scan in one version.
- ``assets/pages/<page_id>/<stage>/<processor>/<version_id>/`` holds one page version.
- ``assets/pages/<page_id>/edits/<processor>/`` holds the manual edits of a page that steps read as inputs.
- ``assets/book/`` holds the results of whole-book steps, such as typesetting and export.

The ``SourceStore`` owns ``incoming/`` and ``sources/`` and the ``AssetStore`` owns ``assets/``, so each port removes
everything of a project it holds with one prefix. Keys name sources, scans and pages by identifier and never by
position, so moving a page keeps its keys. A key never holds an empty, ``.`` or ``..`` segment, so no key built here or
accepted by ``ProjectKeys.owning`` climbs out of its project.
"""

from typing import TYPE_CHECKING, ClassVar, Self
from uuid import UUID

from attrs import frozen

from bookreviver.domain.enums import LabeledStrEnum
from bookreviver.domain.ids import ProjectId, StorageKey

if TYPE_CHECKING:
    from bookreviver.domain.entities import PageVersion, Scan
    from bookreviver.domain.enums import Rendition
    from bookreviver.domain.ids import JobId, PageId, SourceId


class KeySegment(LabeledStrEnum):
    """A fixed directory name of the storage layout."""

    PROJECTS = 'projects', 'Root of every project'
    INCOMING = 'incoming', 'Uploads being received, owned by the source store'
    SOURCES = 'sources', 'Files of the sources as uploaded, owned by the source store'
    ASSETS = 'assets', 'Derived files, owned by the asset store'
    SCANS = 'scans', 'Renditions of the scans'
    PAGES = 'pages', 'Versions and edits of the pages of the book'
    EDITS = 'edits', 'Manual edits of a page'
    BOOK = 'book', 'Results of whole-book steps'


@frozen
class ProjectKeys:
    """The part of the storage key space that belongs to one project.

    :ivar project_id: Project whose keys are built.
    """

    project_id: ProjectId

    SEPARATOR: ClassVar[str] = '/'
    # Prefix of the directory of one version of a scan's renditions, as in ``v1``
    VERSION_PREFIX: ClassVar[str] = 'v'
    # A key made of the root, the project and at least one name inside the project
    MIN_SEGMENTS: ClassVar[int] = 3
    # Segments that would address the parent of a key or climb out of it
    UNSAFE_SEGMENTS: ClassVar[frozenset[str]] = frozenset({'', '.', '..'})

    @property
    def prefix(self) -> StorageKey:
        """The prefix shared by every key of the project, ending with the separator."""
        return StorageKey(f'{self._key()}{self.SEPARATOR}')

    @property
    def incoming_area(self) -> StorageKey:
        """The directory of every upload of the project being received."""
        return self._key(KeySegment.INCOMING)

    @property
    def sources_area(self) -> StorageKey:
        """The directory of every source of the project."""
        return self._key(KeySegment.SOURCES)

    @property
    def assets_area(self) -> StorageKey:
        """The directory of every derived file of the project."""
        return self._key(KeySegment.ASSETS)

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

    def scan_rendition(self, scan: Scan, rendition: Rendition) -> StorageKey:
        """Return the key of one rendition of a scan in the scan's current version of renditions.

        :param scan: Scan of the project, whose source, number and renditions version place the file.
        :type scan: Scan
        :param rendition: Derived file of the scan.
        :type rendition: Rendition
        :returns: Key of ``assets/scans/<source_id>/<number>/v<version>/<rendition>``.
        :rtype: StorageKey
        :raises ValueError: If the scan belongs to another project.
        """
        if scan.project_id != self.project_id:
            err_msg = f'Scan {scan.id} belongs to project {scan.project_id}, not {self.project_id}.'
            raise ValueError(err_msg)
        return self._key(
            KeySegment.ASSETS,
            KeySegment.SCANS,
            str(scan.source_id),
            str(scan.number),
            f'{self.VERSION_PREFIX}{scan.renditions.version}',
            rendition,
        )

    def page(self, page_id: PageId) -> StorageKey:
        """Return the directory of every version and edit of one page of the book, removed with the page.

        :param page_id: Page whose files the directory holds.
        :type page_id: PageId
        :returns: Key of ``assets/pages/<page_id>``.
        :rtype: StorageKey
        """
        return self._key(KeySegment.ASSETS, KeySegment.PAGES, str(page_id))

    def version_rendition(self, version: PageVersion, rendition: Rendition) -> StorageKey:
        """Return the key of one rendition of a page version, the version being a page of this project.

        :param version: Page version, whose page, stage, processor and identifier place the file.
        :type version: PageVersion
        :param rendition: Derived file of the version.
        :type rendition: Rendition
        :returns: Key of ``assets/pages/<page_id>/<stage>/<processor>/<version_id>/<rendition>``.
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
            rendition,
        )

    def page_edits(self, page_id: PageId, processor_key: str) -> StorageKey:
        """Return the directory of the manual edits of one page that one processor reads.

        :param page_id: Page the edits belong to.
        :type page_id: PageId
        :param processor_key: Key of the processor reading the edits, such as ``cleanup.eraser``.
        :type processor_key: str
        :returns: Key of ``assets/pages/<page_id>/edits/<processor>``.
        :rtype: StorageKey
        :raises ValueError: If the processor key is not a safe directory name.
        """
        return self._key(KeySegment.ASSETS, KeySegment.PAGES, str(page_id), KeySegment.EDITS, processor_key)

    @classmethod
    def owning(cls, key: StorageKey) -> Self | None:
        """Return the key space that ``key`` belongs to, or None when it is not a well-formed key inside a project.

        :param key: Key to attribute, such as one a client sent.
        :type key: StorageKey
        :returns: The keys of the project holding ``key``, or None for a key outside every project, naming a project
                  itself, or holding an empty, ``.`` or ``..`` segment.
        :rtype: Self | None
        """
        segments = key.split(cls.SEPARATOR)
        if (
            len(segments) < cls.MIN_SEGMENTS
            or segments[0] != KeySegment.PROJECTS
            or cls.UNSAFE_SEGMENTS.intersection(segments)
        ):
            return None
        try:
            project_id = UUID(segments[1])
        except ValueError:
            return None
        return cls(ProjectId(project_id))

    def _key(self, *segments: str) -> StorageKey:
        """Join ``segments`` under the project's directory into a key.

        :param segments: Names below ``projects/<project_id>``, outermost first.
        :type segments: str
        :returns: The key of ``projects/<project_id>/<segments>``.
        :rtype: StorageKey
        :raises ValueError: If a segment is empty, ``.`` or ``..``, or holds the separator.
        """
        if unsafe := [segment for segment in segments if segment in self.UNSAFE_SEGMENTS or self.SEPARATOR in segment]:
            err_msg = f'Storage key segments may not be empty, "." or "..", or hold "{self.SEPARATOR}": {unsafe}.'
            raise ValueError(err_msg)
        return StorageKey(self.SEPARATOR.join((KeySegment.PROJECTS, str(self.project_id), *segments)))
