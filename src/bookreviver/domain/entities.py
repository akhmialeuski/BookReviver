"""Entities: domain objects with an identity, frozen and changed through ``attrs.evolve``."""

import hashlib
import json
import re
from typing import TYPE_CHECKING, ClassVar

from attrs import field, frozen, validators

from bookreviver.domain.enums import ImagePolicy, JobState, PageKind, PageOrigin, VersionState
from bookreviver.domain.ids import PageVersionId
from bookreviver.domain.values import SHA256_PATTERN, MetadataSuggestion, Progress, Renditions, Transform

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from bookreviver.domain.enums import FileType, JobKind, SourceKind, Stage
    from bookreviver.domain.ids import AccountId, JobId, PageId, ProjectId, ScanId, SourceId
    from bookreviver.domain.values import BookDetails, MetadataMap, ProcessorRef, ScanFacts, SourceFile

# The length of a page version identifier: a hash cut to 16 hexadecimal digits
VERSION_ID_LENGTH: int = 16
VERSION_ID_PATTERN: str = rf'[0-9a-f]{{{VERSION_ID_LENGTH}}}'


@frozen(kw_only=True)
class Actor:
    """The signed-in account on whose behalf a use case runs.

    :ivar account_id: Identifier of the signed-in account.
    """

    account_id: AccountId


@frozen(kw_only=True)
class Project:
    """One book being digitised, owned by one account.

    The project holds the description of the book and the settings of the work on it. The files the book was
    assembled from are its sources, which the project does not name.

    :ivar id: Identifier of the project.
    :ivar owner_id: Account owning the project, the only one that may read or change it.
    :ivar details: Bibliographic description of the book.
    :ivar image_policy: How the ``full`` images of the project's scans and page versions are stored.
    :ivar cover_page_id: Page whose thumbnail the project list shows, or None for the first page of the book.
    :ivar created_at: When the project was created.
    :ivar updated_at: When the project was last changed, which orders the owner's project list.
    """

    id: ProjectId
    owner_id: AccountId
    details: BookDetails
    image_policy: ImagePolicy = ImagePolicy.COMPACT
    cover_page_id: PageId | None = None
    created_at: datetime
    updated_at: datetime

    def is_owned_by(self, actor: Actor) -> bool:
        """Return whether the actor owns this project.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :returns: True when the actor's account is the project's owner.
        :rtype: bool
        """
        return self.owner_id == actor.account_id


@frozen(kw_only=True)
class ProjectOverview:
    """A project as shown in a list, with the size of its book, counted when read.

    :ivar project: The listed project.
    :ivar page_count: Number of pages of the book, without the pages kept out of it.
    :ivar source_count: Number of sources the book was assembled from.
    :ivar scan_count: Number of scans in all the sources.
    """

    project: Project
    page_count: int = field(default=0, validator=validators.ge(0))
    source_count: int = field(default=0, validator=validators.ge(0))
    scan_count: int = field(default=0, validator=validators.ge(0))


@frozen(kw_only=True)
class Source:
    """One uploaded file of a book, or the files of one indirect DjVu document, never changed after its import.

    :ivar id: Identifier of the source, which also names its storage directory.
    :ivar project_id: Project owning the source.
    :ivar kind: Kind of the source, which selects the format that reads it.
    :ivar file_type: Exact format of the main file.
    :ivar file_name: Name the browser sent, with the relative path of a directory upload sanitised.
    :ivar files: Stored files, the main file first; more than one only for an indirect DjVu document.
    :ivar size_bytes: Total size of the files in bytes.
    :ivar sha256: SHA-256 digest of the main file, which refuses a second upload of the same file to the project.
    :ivar scan_count: Number of scans the source holds.
    :ivar metadata: Technical metadata of the format, which stays here and is never copied into the project.
    :ivar suggestion: Values of the book description found in the file, which fill only empty description fields.
    :ivar import_job_id: Import job that created the source, or None once that job is deleted.
    :ivar imported_at: When the source became part of the project.
    """

    id: SourceId
    project_id: ProjectId
    kind: SourceKind
    file_type: FileType
    file_name: str = field(validator=validators.min_len(1))
    files: Sequence[SourceFile] = field(validator=validators.min_len(1))
    size_bytes: int = field(validator=validators.ge(0))
    sha256: str = field(validator=validators.matches_re(SHA256_PATTERN))
    scan_count: int = field(validator=validators.ge(0))
    metadata: MetadataMap = field(factory=dict)
    suggestion: MetadataSuggestion = field(factory=MetadataSuggestion)
    import_job_id: JobId | None = None
    imported_at: datetime


@frozen(kw_only=True)
class Scan:
    """One image of a source as the file holds it: a page of a PDF or DjVu document, or a frame of an image file.

    A scan is created by the import and never changes, apart from the state of its derived files.

    :ivar id: Identifier of the scan.
    :ivar project_id: Project owning the scan's source, which lists the scans of a book without reading its sources.
    :ivar source_id: Source holding the scan.
    :ivar number: Position of the scan in its source, starting at 0.
    :ivar source_label: Page label the file itself gives, such as the PDF page label ``xii``, or empty.
    :ivar facts: Technical facts of the image.
    :ivar renditions: Readiness and version of the derived files.
    """

    id: ScanId
    project_id: ProjectId
    source_id: SourceId
    number: int = field(validator=validators.ge(0))
    source_label: str = ''
    facts: ScanFacts
    renditions: Renditions = field(factory=Renditions)


@frozen(kw_only=True)
class Page:
    """A page of the book in book order, which stands on its own once created.

    A page is cut from a scan by the page split, or added by the user in the page order stage as a blank leaf or a
    placeholder. It keeps its own copy of its image in its base version, so nothing about it depends on its source,
    and its identity survives reordering, splitting again and re-running stages. Its position is the order of its key
    among the keys of the project's pages, and the position number shown to the user is computed when it is read.

    :ivar id: Identifier of the page, which names its storage directory.
    :ivar project_id: Project owning the page.
    :ivar order_key: Fractional index string whose byte order is the order of the pages in the book.
    :ivar label: Printed number, such as ``xii``, ``12`` or ``[4]``, or empty for an unnumbered page.
    :ivar kind: Role of the page in the book.
    :ivar origin: Where the image of the page comes from.
    :ivar scan_id: Scan the page was cut from, or None for a blank leaf, a placeholder, or a page whose source was
                   deleted.
    :ivar slot: Part of the scan the page shows: ``0`` the whole scan, ``1`` and ``2`` the halves of a spread, higher
                for a fold-out.
    :ivar included: Whether the page is part of the book; off for a colour chart or a duplicate.
    :ivar notes: Notes of the user.
    :ivar created_at: When the page was created.
    :ivar updated_at: When the page was last changed.
    """

    WHOLE_SCAN: ClassVar[int] = 0

    id: PageId
    project_id: ProjectId
    order_key: str = field(validator=validators.min_len(1))
    label: str = ''
    kind: PageKind = PageKind.TEXT
    origin: PageOrigin
    scan_id: ScanId | None = None
    slot: int = field(default=WHOLE_SCAN, validator=validators.ge(WHOLE_SCAN))
    included: bool = True
    notes: str = ''
    created_at: datetime
    updated_at: datetime

    def __attrs_post_init__(self) -> None:
        """Check that only a page cut from a scan names a scan.

        :raises ValueError: If a blank leaf or a placeholder names a scan.
        """
        if self.scan_id is not None and self.origin is not PageOrigin.SCAN:
            err_msg = f'A page of origin {self.origin} has no scan, but names scan {self.scan_id}.'
            raise ValueError(err_msg)


@frozen(kw_only=True)
class PageVersion:
    """The result of one processing step on one page of the book, never changed once ready.

    A version is identified by a hash of the page, the processor, its parameters, the input version and the manual
    edit, so equal work gets the same identifier and its cached result. The first version of a page, its base version,
    has no input version: its input is a scan or, for a blank leaf, nothing.

    :ivar id: Identifier of the version, the hash of what produced it.
    :ivar page_id: Page of the book the version belongs to.
    :ivar stage: Stage of the step.
    :ivar processor: Key and version of the processor that ran the step.
    :ivar input_id: Version the step read, or None for a base version.
    :ivar params: Parameters of the step, following the processor's JSON Schema.
    :ivar transform: Transform of coordinates from the input to this version.
    :ivar data: Data the step found, such as an angle, a frame or a confidence.
    :ivar renditions: State of the version's image files, or None for a step without an image.
    :ivar state: Where the version is in its lifecycle.
    :ivar created_at: When the version was created.
    """

    id: PageVersionId
    page_id: PageId
    stage: Stage
    processor: ProcessorRef
    input_id: PageVersionId | None = None
    params: MetadataMap = field(factory=dict)
    transform: Transform = field(factory=Transform)
    data: MetadataMap = field(factory=dict)
    renditions: Renditions | None = field(factory=Renditions)
    state: VersionState = VersionState.PENDING
    created_at: datetime

    def __attrs_post_init__(self) -> None:
        """Check the identifier and the renditions, which place the version's files in storage.

        :raises ValueError: If the identifier is not 16 lower-case hexadecimal digits, or the renditions are past their
                            first version, since a version is written once into the directory of its identifier.
        """
        if not re.fullmatch(VERSION_ID_PATTERN, self.id):
            err_msg = f'A page version id is 16 lower-case hexadecimal digits, not {self.id!r}.'
            raise ValueError(err_msg)
        if self.renditions is not None and self.renditions.version != Renditions.FIRST_VERSION:
            err_msg = 'The renditions of a page version are written once and stay at their first version.'
            raise ValueError(err_msg)

    @staticmethod
    def identify(
        *,
        page_id: PageId,
        processor: ProcessorRef,
        params: MetadataMap | None = None,
        input_id: PageVersionId | None = None,
    ) -> PageVersionId:
        """Return the identifier of the version a step produces, a hash of everything that produced it.

        Equal work gets the same identifier, so a repeated step finds the files of its earlier result. The hash covers
        the page, the processor key and version, the parameters and the input version; manual edits join it when
        they exist.

        :param page_id: Page the step runs on.
        :type page_id: PageId
        :param processor: Key and version of the processor running the step.
        :type processor: ProcessorRef
        :param params: Parameters of the step, which must be JSON-compatible, or None for none.
        :type params: MetadataMap | None
        :param input_id: Version the step reads, or None for a base version.
        :type input_id: PageVersionId | None
        :returns: The hash of the arguments, cut to 16 lower-case hexadecimal digits.
        :rtype: PageVersionId
        """
        produced_by = [str(page_id), processor.key, processor.version, params or {}, input_id]
        digest = hashlib.sha256(json.dumps(produced_by, sort_keys=True, separators=(',', ':')).encode())
        return PageVersionId(digest.hexdigest()[:VERSION_ID_LENGTH])


@frozen(kw_only=True)
class Job:
    """A background job and how far it has come.

    :ivar id: Identifier of the job.
    :ivar project_id: Project the job works on.
    :ivar kind: What the job does, which also selects its worker pool.
    :ivar state: Where the job is in its life cycle.
    :ivar progress: How many of its steps are done.
    :ivar error: Why the job failed, shown to the user, or empty.
    :ivar created_at: When the job was recorded.
    :ivar started_at: When a worker started the job, or None while it is queued.
    :ivar finished_at: When the job reached a final state, or None before.
    """

    id: JobId
    project_id: ProjectId
    kind: JobKind
    state: JobState = JobState.QUEUED
    progress: Progress = field(factory=Progress)
    error: str = ''
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
