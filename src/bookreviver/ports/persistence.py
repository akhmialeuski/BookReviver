"""Persistence ports: repositories per aggregate and the unit of work that commits them together.

Services never see a database. They open a ``UnitOfWork``, read and change entities through its repositories, and
commit, so every change of one use case lands in one transaction. The in-memory and SQLAlchemy adapters both run the
contract suite in ``tests/contracts``, which is what makes them interchangeable.

The book is the aggregate. Every change of a book runs in one block of the unit of work, ``change_book``, which waits
for any other change of that book, holds it until the block commits, and so reads only what the previous change
committed. A change outside the content of a book runs in ``change``. A repository write outside both blocks is a
programming error, reported as ``NoChangeOpenError``.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, override

from bookreviver.domain.entities import (
    BookPlace,
    Job,
    Page,
    PageStage,
    PageStepChange,
    PageStepState,
    PageVersion,
    PaginationSection,
    Project,
    Recipe,
    RecipeProfile,
    ResultMarkChange,
    Scan,
    Source,
)
from bookreviver.domain.ids import (
    JobId,
    PageId,
    PageStepChangeId,
    PageVersionId,
    PaginationSectionId,
    ProjectId,
    RecipeId,
    RecipeProfileId,
    ResultMarkChangeId,
    ScanId,
    SourceId,
)
from bookreviver.domain.step_values import StepValues, StepValuesKey
from bookreviver.domain.values import BookPlaceKey, PageStageKey, PageStepKey

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence
    from contextlib import AbstractAsyncContextManager
    from datetime import datetime

    from bookreviver.domain.entities import ProjectOverview
    from bookreviver.domain.enums import JobState, RecipeKind, ResultMark, Side, Stage, VersionScale
    from bookreviver.domain.ids import AccountId, ChangeBatchId, StepId
    from bookreviver.domain.stage_summaries import StageTally, StepTally
    from bookreviver.domain.values import PageSize, ProcessorRef, Slice, SliceRequest

# How long, in seconds, a change waits for another change of the same book before it gives up with ``BookBusyError``
DEFAULT_CHANGE_WAIT_SECONDS: float = 30.0


class NoChangeOpenError(RuntimeError):
    """A repository wrote while no ``change_book`` or ``change`` block was open.

    It is a programming error and not a ``DomainError``, so it reaches the caller as a failure and no request can
    cause it.
    """

    def __init__(self) -> None:
        """Report the write with its fixed sentence."""
        super().__init__('A repository write needs an open change_book or change block.')


class NestedChangeError(RuntimeError):
    """A ``change_book`` or ``change`` block was opened inside another one of the same unit of work.

    It is a programming error and not a ``DomainError``. A second block inside the first would wait for a lock the
    first one holds, so it is refused at once instead.
    """

    def __init__(self) -> None:
        """Report the nesting with its fixed sentence."""
        super().__init__('A change_book or change block is already open on this unit of work.')


class Repository[EntityT, IdT](ABC):
    """Storage of one kind of entity addressed by its identifier."""

    @abstractmethod
    async def get(self, entity_id: IdT) -> EntityT:
        """Return the entity.

        :param entity_id: Identifier of the entity.
        :type entity_id: IdT
        :returns: The stored entity.
        :rtype: EntityT
        :raises NotFoundError: If no entity has this identifier.
        """

    @abstractmethod
    async def add(self, entity: EntityT) -> EntityT:
        """Store a new entity and return it as stored.

        :param entity: Entity to store, with its identifier already assigned.
        :type entity: EntityT
        :returns: The entity as stored.
        :rtype: EntityT
        :raises ConflictError: If an entity with this identifier, or with a value its port declares unique, is
                               already stored.
        :raises NotFoundError: If an entity it refers to, such as its project, is not stored.
        """

    @abstractmethod
    async def add_many(self, entities: Sequence[EntityT]) -> Sequence[EntityT]:
        """Store several new entities at once, all of them or, on an error, none.

        After an error the unit of work is rolled back before it is used again, as a database requires.

        :param entities: Entities to store, with their identifiers already assigned.
        :type entities: Sequence[EntityT]
        :returns: The entities as stored, in the given order.
        :rtype: Sequence[EntityT]
        :raises ConflictError: If an identifier or a unique value of one entity is stored already or given twice.
        :raises NotFoundError: If an entity one of them refers to is not stored.
        """

    @abstractmethod
    async def update(self, entity: EntityT) -> EntityT:
        """Replace the stored state of an existing entity and return it as stored.

        :param entity: Entity with its new state.
        :type entity: EntityT
        :returns: The entity as stored.
        :rtype: EntityT
        :raises NotFoundError: If the entity, or an entity it refers to, is not stored.
        :raises ConflictError: If its new state takes a value its port declares unique from another entity.
        """

    @abstractmethod
    async def delete(self, entity_id: IdT) -> None:
        """Remove the entity, together with everything that belongs to it.

        :param entity_id: Identifier of the entity.
        :type entity_id: IdT
        :raises NotFoundError: If no entity has this identifier.
        """


class ProjectRepository(Repository[Project, ProjectId]):
    """Projects, listed per owning account.

    A project's cover is one of its own pages: ``add`` and ``update`` raise ``NotFoundError`` naming a cover page that
    is not stored or belongs to another project, and deleting the cover page leaves the project without a cover.
    """

    @abstractmethod
    async def list_for_owner(self, owner_id: AccountId, request: SliceRequest) -> Slice[ProjectOverview]:
        """Return the owner's projects with their counts, most recently updated first, ties by identifier.

        :param owner_id: Account owning the projects.
        :type owner_id: AccountId
        :param request: Offset and limit of the window to return.
        :type request: SliceRequest
        :returns: The projects of the window with the counts of their included pages, sources and scans, and the
                  number of all the owner's projects.
        :rtype: Slice[ProjectOverview]
        """

    @abstractmethod
    async def overview(self, project: Project) -> ProjectOverview:
        """Return a project read earlier together with the counts of its book.

        :param project: Project whose book is counted.
        :type project: Project
        :returns: The project with the counts of its included pages, sources and scans.
        :rtype: ProjectOverview
        """


class SourceRepository(Repository[Source, SourceId]):
    """Sources of the projects; the pair of a project and the digest of a source's main file is unique.

    Deleting a source removes its scans, and the pages of the book made from them stay without their scan.
    """

    @abstractmethod
    async def list_for_project(self, project_id: ProjectId) -> Sequence[Source]:
        """Return the project's sources in the order they were imported, ties by identifier.

        :param project_id: Project owning the sources.
        :type project_id: ProjectId
        :returns: Every source of the project, the earliest import first.
        :rtype: Sequence[Source]
        """

    @abstractmethod
    async def find_by_sha256(self, project_id: ProjectId, sha256: str) -> Source | None:
        """Return the project's source whose main file has this digest, which refuses a second upload of the file.

        :param project_id: Project owning the sources.
        :type project_id: ProjectId
        :param sha256: SHA-256 digest of a main file as lower-case hexadecimal digits.
        :type sha256: str
        :returns: The source with this digest, or None when the project has none.
        :rtype: Source | None
        """


class ScanRepository(Repository[Scan, ScanId]):
    """Scans of the sources; the pair of a source and the number of a scan in it is unique."""

    @abstractmethod
    async def list_for_source(self, source_id: SourceId) -> Sequence[Scan]:
        """Return the scans of one source in their order in the source.

        :param source_id: Source holding the scans.
        :type source_id: SourceId
        :returns: Every scan of the source, by number.
        :rtype: Sequence[Scan]
        """

    @abstractmethod
    async def list_for_project(self, project_id: ProjectId, request: SliceRequest) -> Slice[Scan]:
        """Return the scans of a project, source by source in import order and by number within a source.

        :param project_id: Project owning the scans.
        :type project_id: ProjectId
        :param request: Offset and limit of the window to return.
        :type request: SliceRequest
        :returns: The scans of the window and the number of all the project's scans.
        :rtype: Slice[Scan]
        """

    @abstractmethod
    async def list_by_ids(self, scan_ids: Collection[ScanId]) -> Sequence[Scan]:
        """Return several scans in one read, so a window of the book costs one query to learn its sources.

        :param scan_ids: Scans to read; one that is not stored is left out of the result.
        :type scan_ids: Collection[ScanId]
        :returns: The stored scans among them, in no particular order.
        :rtype: Sequence[Scan]
        """

    @abstractmethod
    async def list_unready(self, project_id: ProjectId) -> Sequence[Scan]:
        """Return the scans of a project whose renditions are not ready, which an import cut again.

        :param project_id: Project owning the scans.
        :type project_id: ProjectId
        :returns: The scans without ready renditions, source by source in import order and by number within a source.
        :rtype: Sequence[Scan]
        """


class PageRepository(Repository[Page, PageId]):
    """Pages of the book, addressed by their identifier and ordered by their order key.

    Within a project an order key is unique, and a part of a scan, the pair of a scan and a slot, belongs to one page
    at most. A page keeps its row when its scan is deleted, and loses only the reference to it. Deleting a page
    removes its versions.

    A page carries a ``revision`` that every update raises by one. An update whose page has not the revision stored is
    a write over a change the caller never read, so it is refused with a ``ConcurrentChangeError`` and writes nothing.
    """

    @abstractmethod
    async def list_for_project(
        self, project_id: ProjectId, request: SliceRequest, *, included_only: bool = False
    ) -> Slice[Page]:
        """Return the pages of a project in book order, the byte order of their order keys.

        The position of a page in the book is the offset of the window plus its index in the window.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param request: Offset and limit of the window to return.
        :type request: SliceRequest
        :param included_only: Whether to leave out the pages kept out of the book, which then take no part in the
                              offset either.
        :type included_only: bool
        :returns: The pages of the window and the number of all the project's pages, or of its included pages when
                  ``included_only`` is set.
        :rtype: Slice[Page]
        """

    @abstractmethod
    async def list_by_ids(self, project_id: ProjectId, page_ids: Collection[PageId]) -> Sequence[Page]:
        """Return the given pages of a project in book order.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param page_ids: Identifiers of the pages to read, repeats counting once.
        :type page_ids: Collection[PageId]
        :returns: The pages, in the byte order of their order keys.
        :rtype: Sequence[Page]
        :raises NotFoundError: If an identifier names no page, or a page of another project, which is reported like a
                               missing one.
        """

    @abstractmethod
    async def list_range(self, project_id: ProjectId, first_key: str, last_key: str) -> Sequence[Page]:
        """Return the pages of a project whose order keys lie between two keys, both included, in book order.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param first_key: Smallest order key of the range, in byte order.
        :type first_key: str
        :param last_key: Greatest order key of the range, in byte order.
        :type last_key: str
        :returns: The pages from ``first_key`` to ``last_key``, none when the range is reversed.
        :rtype: Sequence[Page]
        """

    @abstractmethod
    async def list_for_source(self, project_id: ProjectId, source_id: SourceId) -> Sequence[Page]:
        """Return the pages whose scans belong to a source, in book order.

        Pages without a scan, such as placeholders and blank leaves, belong to no source and are never listed.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param source_id: Source whose scans the pages show.
        :type source_id: SourceId
        :returns: The pages cut from the scans of the source, by order key.
        :rtype: Sequence[Page]
        """

    @abstractmethod
    async def neighbour_key(
        self, project_id: ProjectId, key: str, side: Side, *, excluding: Collection[PageId] = ()
    ) -> str | None:
        """Return the order key of the page next to ``key`` on one side, leaving some pages out of the count.

        A group of pages that is about to move is left out, so the key found is the one of the page the group will
        stand next to once it has left its old place.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param key: Order key the neighbour is looked up from, usually the key of an anchor page.
        :type key: str
        :param side: Whether to look for the nearest key before or after ``key``.
        :type side: Side
        :param excluding: Pages that do not count as neighbours.
        :type excluding: Collection[PageId]
        :returns: The nearest key on that side, or None when no page lies there, which is the start or the end of the
                  book.
        :rtype: str | None
        """

    @abstractmethod
    async def update_many(self, pages: Sequence[Page]) -> None:
        """Replace the stored state of several pages in the one transaction, all of them or, on an error, none.

        After an error the unit of work is rolled back before it is used again, as a database requires.

        :param pages: Pages with their new state.
        :type pages: Sequence[Page]
        :raises NotFoundError: If a page is not stored.
        :raises ConflictError: If the new state of a page takes an order key, or a part of a scan, that another page
                               of the project has, such as a page moved to the place another move took in the
                               meantime.
        :raises ConcurrentChangeError: If another transaction changed a page after it was read, which its revision no
                                       longer matches.
        """

    @abstractmethod
    async def list_for_scan(self, scan_id: ScanId) -> Sequence[Page]:
        """Return the pages cut from one scan, by their slot.

        :param scan_id: Scan the pages were cut from.
        :type scan_id: ScanId
        :returns: Every page that names the scan, the whole scan or its parts in slot order.
        :rtype: Sequence[Page]
        """

    @abstractmethod
    async def count_before(self, page: Page) -> int:
        """Return how many pages of the page's project come before it in book order, which is its position.

        Every page counts, whether it is included in the book or not, as in the windows ``list_for_project`` returns.

        :param page: Stored page of the project.
        :type page: Page
        :returns: The number of the project's pages whose order key is smaller than the page's, from zero.
        :rtype: int
        """

    @abstractmethod
    async def last_order_key(self, project_id: ProjectId) -> str | None:
        """Return the order key of the last page of a project, after which new pages are appended.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :returns: The greatest order key of the project's pages, or None for a book without pages.
        :rtype: str | None
        """

    @abstractmethod
    async def kind_tally(self, project_id: ProjectId) -> Mapping[RecipeKind, int]:
        """Count the pages with an image of each kind of a project, in one grouped query.

        The kind of a page is ``Page.recipe_kind``. A placeholder has no image and is never counted.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :returns: The number of pages of each kind that has any page.
        :rtype: Mapping[RecipeKind, int]
        """


class PaginationSectionRepository(Repository[PaginationSection, PaginationSectionId]):
    """The pagination sections of the books, which a page of the book is numbered from.

    A section names the page it starts at. Deleting that page, or the project, removes the section with it, so a use
    case that deletes pages hands the section to the next page first.
    """

    @abstractmethod
    async def list_for_project(self, project_id: ProjectId) -> Sequence[PaginationSection]:
        """Return the sections of a project in the order they were made, ties by identifier.

        The sections are not in book order, since the order of the book is the order of the pages they start at.

        :param project_id: Project owning the sections.
        :type project_id: ProjectId
        :returns: Every section of the project.
        :rtype: Sequence[PaginationSection]
        """


class PageVersionRepository(Repository[PageVersion, PageVersionId]):
    """Versions of the pages of the book; a version whose input version is deleted keeps its row without the input."""

    @abstractmethod
    async def list_for_page(self, page_id: PageId) -> Sequence[PageVersion]:
        """Return the versions of one page in the order they were created, ties by identifier.

        :param page_id: Page owning the versions.
        :type page_id: PageId
        :returns: Every version of the page, the earliest first.
        :rtype: Sequence[PageVersion]
        """

    @abstractmethod
    async def list_to_prepare(self, project_id: ProjectId, processor_keys: Collection[str]) -> Sequence[PageVersion]:
        """Return the versions of a project whose files still have to be written: the pending and the failed ones.

        A failed version is returned with the pending ones, so the next job of the project tries it again and no route
        of its own is needed to repeat it.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param processor_keys: Keys of the processors whose versions are returned, those the job knows how to make.
        :type processor_keys: Collection[str]
        :returns: The pending and failed versions made by those processors, the earliest first, ties by identifier.
        :rtype: Sequence[PageVersion]
        """

    @abstractmethod
    async def base_sizes(self, project_id: ProjectId) -> Sequence[PageSize]:
        """Return the sizes of the page split's base versions of the included pages that show a scan.

        The median of these is the size of a generated blank leaf. A leaf drawn in place of a scan is a version of the
        page order, so it is not counted. A version that records no size is left out.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :returns: The recorded size of every such base version, in no particular order.
        :rtype: Sequence[PageSize]
        """

    @abstractmethod
    async def list_base_versions(self, page_ids: Collection[PageId]) -> Sequence[PageVersion]:
        """Return the base versions of several pages in one read, so a window of a book costs one query.

        A base version is a version without an input version, the one a scan or nothing feeds. A page cut from a scan
        again gets a new base version beside the old one, so a page can have several.

        :param page_ids: Pages whose base versions are read.
        :type page_ids: Collection[PageId]
        :returns: The base versions of those pages, the earliest first, ties by identifier.
        :rtype: Sequence[PageVersion]
        """

    @abstractmethod
    async def list_by_ids(self, version_ids: Collection[PageVersionId]) -> Sequence[PageVersion]:
        """Return the stored versions among the given identifiers, in one read.

        :param version_ids: Identifiers of the versions to read, of which those that are not stored are left out.
        :type version_ids: Collection[PageVersionId]
        :returns: The versions found, the earliest first, ties by identifier.
        :rtype: Sequence[PageVersion]
        """

    @abstractmethod
    async def find(self, version_id: PageVersionId) -> PageVersion | None:
        """Return the version with this identifier, which a repeated run reuses instead of computing it again.

        :param version_id: Identifier of the version, the hash of what produces it.
        :type version_id: PageVersionId
        :returns: The stored version, ready or failed or still running, or None when no run has made it.
        :rtype: PageVersion | None
        """

    @abstractmethod
    async def list_for_stage(
        self,
        page_id: PageId,
        stage: Stage | None,
        scale: VersionScale | None,
        request: SliceRequest,
        mark: ResultMark | None = None,
    ) -> Slice[PageVersion]:
        """Return a window of the versions of one page, filtered by stage, scale and mark, the earliest first.

        :param page_id: Page owning the versions.
        :type page_id: PageId
        :param stage: Stage whose versions are listed, or None for every stage.
        :type stage: Stage | None
        :param scale: Scale of the runs listed, or None for both.
        :type scale: VersionScale | None
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :param mark: Mark the versions carry, or None for every version, marked or not.
        :type mark: ResultMark | None
        :returns: The versions of the window and the number of all that match, ties by identifier.
        :rtype: Slice[PageVersion]
        """

    @abstractmethod
    async def collectable(self, project_id: ProjectId, previews_older_than: datetime) -> Sequence[PageVersion]:
        """Return the versions a collection may delete: not base, and read by no version that stays.

        A version stays when a stage record names it as its head, when it is a base version, which has no input, when
        it is a preview too young to go, when the user marked it Good or wrote a comment on it, and when a version that
        stays reads it, directly or through the chain of inputs. Deleting an input would leave the version that reads
        it with no input, which the database allows and which would make it look like a base version for ever. A full
        run goes as soon as it is none of these, with no age to wait for, and a preview once it was created before
        ``previews_older_than``, since the editor that asked for it is still showing it.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param previews_older_than: Previews created before this moment may go.
        :type previews_older_than: datetime
        :returns: The versions that may be deleted, the earliest first, ties by identifier.
        :rtype: Sequence[PageVersion]
        """

    @abstractmethod
    async def delete_many(self, version_ids: Collection[PageVersionId]) -> None:
        """Remove the rows of several versions in the one transaction; a version that is not stored is left alone.

        The log of the marks of each version goes with it.

        :param version_ids: Versions to remove.
        :type version_ids: Collection[PageVersionId]
        """


class PageStageRepository(Repository[PageStage, PageStageKey]):
    """The current version of each stage of each page; deleting a page removes its records.

    A record keeps its row when its head version or its recipe is deleted, and loses only that reference.
    """

    @abstractmethod
    async def save(self, stage: PageStage) -> PageStage:
        """Store the record of a stage of a page, replacing the one stored.

        :param stage: Record to store.
        :type stage: PageStage
        :returns: The record as stored.
        :rtype: PageStage
        :raises NotFoundError: If the page, the head version or the recipe is not stored.
        """

    @abstractmethod
    async def find(self, key: PageStageKey) -> PageStage | None:
        """Return the record of a stage of a page.

        :param key: Page and stage.
        :type key: PageStageKey
        :returns: The record, or None for a stage that has not run on the page.
        :rtype: PageStage | None
        """

    @abstractmethod
    async def list_for_page(self, page_id: PageId) -> Sequence[PageStage]:
        """Return the records of one page in the order of the stages.

        :param page_id: Page owning the records.
        :type page_id: PageId
        :returns: Every record of the page.
        :rtype: Sequence[PageStage]
        """

    @abstractmethod
    async def list_for_pages(self, page_ids: Collection[PageId]) -> Sequence[PageStage]:
        """Return the records of several pages in one read, by page and then in the order of the stages.

        :param page_ids: Pages whose records are read.
        :type page_ids: Collection[PageId]
        :returns: Every record of those pages.
        :rtype: Sequence[PageStage]
        """

    @abstractmethod
    async def list_for_recipe(self, recipe_id: RecipeId) -> Sequence[PageStage]:
        """Return the records of the pages a recipe processed, by page identifier.

        :param recipe_id: Recipe whose pages are listed.
        :type recipe_id: RecipeId
        :returns: Every record that names the recipe.
        :rtype: Sequence[PageStage]
        """

    @abstractmethod
    async def list_fresh(self) -> Sequence[PageStage]:
        """Return the records of the pages of every book whose current version is up to date, by page and stage.

        :returns: Every record in the fresh state, by page identifier and then in the order of the stages.
        :rtype: Sequence[PageStage]
        """

    @abstractmethod
    async def list_replaced(self, stage: Stage, processor: ProcessorRef) -> Sequence[PageStage]:
        """Return the records of a stage of every book whose current version a replaced version of a processor made.

        :param stage: The stage whose records are read.
        :type stage: Stage
        :param processor: The processor by key and installed version: a record is returned when its current version was
                          made by the same key in another version, whatever the state of the record.
        :type processor: ProcessorRef
        :returns: The records, by page identifier.
        :rtype: Sequence[PageStage]
        """

    @abstractmethod
    async def list_for_project_stage(self, project_id: ProjectId, stage: Stage) -> Sequence[PageStage]:
        """Return the records of one stage over the pages of a project, by page identifier.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param stage: The stage.
        :type stage: Stage
        :returns: Every record of the stage in the project.
        :rtype: Sequence[PageStage]
        """

    @abstractmethod
    async def step_tally(self, project_id: ProjectId) -> Sequence[StepTally]:
        """Count the pages of every stage of a project that a run stopped at each step, in one grouped query.

        Only pages with an image are counted, and a record that failed is left out, since its step belongs to a run that
        did not end. A page run through every step that is on has no step and is not counted.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :returns: One tally for each step of a stage that a page stopped at, in no particular order.
        :rtype: Sequence[StepTally]
        """

    @abstractmethod
    async def head_ids(self, project_id: ProjectId) -> Collection[PageVersionId]:
        """Return the identifiers of the versions that are the current version of a stage of a page of the project.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :returns: The distinct head versions.
        :rtype: Collection[PageVersionId]
        """

    @abstractmethod
    async def tally(self, project_ids: Collection[ProjectId]) -> Sequence[StageTally]:
        """Count the records of every stage of the given projects by state, in one grouped query for all of them.

        Only pages with an image are counted, so a placeholder never is. A page counts as marked for review when the
        current version of its record carries a review mark and the record is not failed. A page counts toward check
        when its record is stale or failed or the page is marked, once however many of these hold. A stage that no page
        has a record of has no tally.

        :param project_ids: Projects whose stages are counted.
        :type project_ids: Collection[ProjectId]
        :returns: One tally for each stage of each project that has a record, in no particular order.
        :rtype: Sequence[StageTally]
        """


class PageStepStateRepository(Repository[PageStepState, PageStepKey]):
    """The settings and the manual edit of each step on each page; deleting a page removes its states."""

    @abstractmethod
    async def save(self, state: PageStepState) -> PageStepState:
        """Store a state, replacing the one the same step has on the same page and stage.

        :param state: State to store.
        :type state: PageStepState
        :returns: The state as stored.
        :rtype: PageStepState
        :raises NotFoundError: If the page is not stored.
        """

    @abstractmethod
    async def find(self, key: PageStepKey) -> PageStepState | None:
        """Return the state of one step on one page.

        :param key: Page, stage and step.
        :type key: PageStepKey
        :returns: The state, or None when the page has neither a setting nor an edit for the step.
        :rtype: PageStepState | None
        """

    @abstractmethod
    async def list_for_page(self, page_id: PageId, stage: Stage | None = None) -> Sequence[PageStepState]:
        """Return the states of one page, those of one stage or of all, by stage and step.

        :param page_id: Page owning the states.
        :type page_id: PageId
        :param stage: Stage whose states are listed, or None for every stage.
        :type stage: Stage | None
        :returns: The states of the page.
        :rtype: Sequence[PageStepState]
        """

    @abstractmethod
    async def list_for_step(
        self, page_ids: Collection[PageId], stage: Stage, step_id: StepId
    ) -> Sequence[PageStepState]:
        """Return the states one step of a stage has on several pages, in one read, by page identifier.

        :param page_ids: Pages whose states are read.
        :type page_ids: Collection[PageId]
        :param stage: Stage of the step.
        :type stage: Stage
        :param step_id: The step of a recipe.
        :type step_id: StepId
        :returns: The states of the step on those of the pages that have one.
        :rtype: Sequence[PageStepState]
        """


class StepValuesRepository(Repository[StepValues, StepValuesKey]):
    """The values of the steps for the odd pages, the even pages and the groups; deleting a project removes them."""

    @abstractmethod
    async def save(self, values: StepValues) -> StepValues:
        """Store values, replacing the ones the same part of the pages has for the same step.

        :param values: Values to store.
        :type values: StepValues
        :returns: The values as stored.
        :rtype: StepValues
        :raises NotFoundError: If the project is not stored.
        """

    @abstractmethod
    async def find(self, key: StepValuesKey) -> StepValues | None:
        """Return the values one part of the pages has for one step.

        :param key: The project, the step and the part of the pages.
        :type key: StepValuesKey
        :returns: The values, or None when the part changes no field of the step.
        :rtype: StepValues | None
        """

    @abstractmethod
    async def list_for_step(self, project_id: ProjectId, step_id: StepId) -> Sequence[StepValues]:
        """Return the values every part of the pages of a project has for one step, by scope and group.

        Books built from one profile share the identifiers of the steps, so the values are those of one project.

        :param project_id: Project owning the step.
        :type project_id: ProjectId
        :param step_id: The step of a recipe.
        :type step_id: StepId
        :returns: The values of the step, which are none when no part changes a field of it.
        :rtype: Sequence[StepValues]
        """

    @abstractmethod
    async def list_for_project(self, project_id: ProjectId) -> Sequence[StepValues]:
        """Return the values of every step of a project, by step, scope and group, which is one read for all stages.

        :param project_id: Project owning the steps.
        :type project_id: ProjectId
        :returns: The values of the project, each naming its stage.
        :rtype: Sequence[StepValues]
        """


class PageStepChangeRepository(Repository[PageStepChange, PageStepChangeId]):
    """The history of the layers of the steps of each page. A change is added once and never rewritten.

    Deleting a page removes its history. ``add`` and ``add_many`` number the changes of a page from one in the order
    they are written, whatever their ``sequence``, and return them as stored.
    """

    @abstractmethod
    async def list_for_page(self, page_id: PageId, stage: Stage | None = None) -> Sequence[PageStepChange]:
        """Return the changes of one page, those of one stage or of all, in the order they were written (by sequence).

        :param page_id: Page the changes were made on.
        :type page_id: PageId
        :param stage: Stage whose changes are listed, or None for every stage.
        :type stage: Stage | None
        :returns: The changes of the page.
        :rtype: Sequence[PageStepChange]
        """

    @abstractmethod
    async def list_for_batch(self, batch_id: ChangeBatchId) -> Sequence[PageStepChange]:
        """Return the changes of one batch, which may span several pages, by page and then by sequence.

        :param batch_id: Identifier the changes of the batch share.
        :type batch_id: ChangeBatchId
        :returns: The changes of the batch.
        :rtype: Sequence[PageStepChange]
        """

    @abstractmethod
    async def list_undoing(self, change_ids: Collection[PageStepChangeId]) -> Sequence[PageStepChange]:
        """Return the changes that take back any of the given changes.

        :param change_ids: Identifiers of the changes that may have been undone.
        :type change_ids: Collection[PageStepChangeId]
        :returns: The undos, by page and then by sequence; none for changes that stand.
        :rtype: Sequence[PageStepChange]
        """

    @abstractmethod
    async def delete_for_step(self, key: PageStepKey) -> int:
        """Delete every change of one step on one page, whatever its layer, source or batch.

        Other steps and other pages keep their changes, and the next change added to the page is numbered after the
        highest sequence the page still holds.

        :param key: The page, the stage and the step.
        :type key: PageStepKey
        :returns: How many changes were deleted.
        :rtype: int
        """


class ResultMarkChangeRepository(Repository[ResultMarkChange, ResultMarkChangeId]):
    """The log of the marks and comments of the results. A change is added once and never rewritten.

    Deleting a version removes its log. ``add`` numbers the changes of a version from one in the order they are written,
    whatever their ``sequence``, and returns them as stored.
    """

    @abstractmethod
    async def list_for_version(self, version_id: PageVersionId) -> Sequence[ResultMarkChange]:
        """Return the changes of the mark and the comment of one version, in the order they were written.

        :param version_id: Version the changes were made on.
        :type version_id: PageVersionId
        :returns: The changes of the version, by sequence.
        :rtype: Sequence[ResultMarkChange]
        """


class RecipeRepository(Repository[Recipe, RecipeId]):
    """Recipes of the projects; a stage of a project has at most one recipe for each kind, which the database keeps.

    Deleting a recipe leaves the page stages it processed without their recipe.
    """

    @abstractmethod
    async def list_for_stage(self, project_id: ProjectId, stage: Stage) -> Sequence[Recipe]:
        """Return the recipes of one stage of a project, by creation, ties by identifier.

        :param project_id: Project owning the recipes.
        :type project_id: ProjectId
        :param stage: The stage.
        :type stage: Stage
        :returns: The recipes of the stage, one for each kind, or none before the stage has been used.
        :rtype: Sequence[Recipe]
        """

    @abstractmethod
    async def list_for_project(self, project_id: ProjectId) -> Sequence[Recipe]:
        """Return the recipes of every stage of a project, in the order of the stages and then as ``list_for_stage``.

        :param project_id: Project owning the recipes.
        :type project_id: ProjectId
        :returns: The recipes of the stages that have been used.
        :rtype: Sequence[Recipe]
        """


class RecipeProfileRepository(Repository[RecipeProfile, RecipeProfileId]):
    """The recipe profiles of the accounts; an account has at most one default profile for each stage.

    Deleting an account removes its profiles. A recipe of a book may refer to the profile it was made from, and
    deleting the profile only empties that reference and leaves the recipe as it is.
    """

    @abstractmethod
    async def list_for_account(self, account_id: AccountId, stage: Stage | None = None) -> Sequence[RecipeProfile]:
        """Return the account's profiles in the order of the stages, then by creation, ties by identifier.

        :param account_id: Account owning the profiles.
        :type account_id: AccountId
        :param stage: The stage whose profiles are wanted, or None for every stage.
        :type stage: Stage | None
        :returns: The profiles of the account.
        :rtype: Sequence[RecipeProfile]
        """

    @abstractmethod
    async def find_default(self, account_id: AccountId, stage: Stage) -> RecipeProfile | None:
        """Return the profile a new book of the account starts a stage with.

        :param account_id: Account owning the profile.
        :type account_id: AccountId
        :param stage: The stage.
        :type stage: Stage
        :returns: The default profile of the stage, or None when the account has not chosen one.
        :rtype: RecipeProfile | None
        """

    @abstractmethod
    async def count_books(self, profile_ids: Collection[RecipeProfileId]) -> Mapping[RecipeProfileId, int]:
        """Count the books that have a recipe made from each of the given profiles, in one read for all of them.

        A book counts once for a profile however many of its recipes were made from it.

        :param profile_ids: Profiles to count the books of.
        :type profile_ids: Collection[RecipeProfileId]
        :returns: The number of books by profile, which has no entry for a profile that no book uses.
        :rtype: Mapping[RecipeProfileId, int]
        """


class JobRepository(Repository[Job, JobId]):
    """Background jobs; a project has at most one import job queued or running at a time."""

    @abstractmethod
    @override
    async def add(self, entity: Job) -> Job:
        """Store a new job of a stored project and return it as stored.

        :param entity: Job to store, with its identifier already assigned.
        :type entity: Job
        :returns: The job as stored.
        :rtype: Job
        :raises ConflictError: If a job with this identifier is already stored, or the job is an import that is queued
                               or running and the project already has one; the database enforces the second rule
                               with a partial unique index, so two uploads that both passed a check before either
                               committed cannot both be stored.
        :raises NotFoundError: If the job's project is not stored.
        """

    @abstractmethod
    async def list_for_project(self, project_id: ProjectId, states: Collection[JobState]) -> Sequence[Job]:
        """Return the project's jobs in one of the given states, newest first.

        :param project_id: Project owning the jobs.
        :type project_id: ProjectId
        :param states: States a returned job may be in.
        :type states: Collection[JobState]
        :returns: The matching jobs, most recently created first.
        :rtype: Sequence[Job]
        """

    @abstractmethod
    async def list_for_projects(
        self, project_ids: Collection[ProjectId], states: Collection[JobState]
    ) -> Sequence[Job]:
        """Return the jobs of several projects in one of the given states, in one query for all of them.

        :param project_ids: Projects owning the jobs.
        :type project_ids: Collection[ProjectId]
        :param states: States a returned job may be in.
        :type states: Collection[JobState]
        :returns: The matching jobs of all the projects, most recently created first.
        :rtype: Sequence[Job]
        """

    @abstractmethod
    async def update_if_state(self, entity: Job, *, expected: Collection[JobState]) -> Job | None:
        """Replace the stored job only while its state is one of ``expected``, checking and writing as one step.

        A job changes state from two places at once, a worker finishing it and an account holder cancelling it, so
        a read followed by ``update`` could overwrite a state committed in between. Here the check reads the latest
        committed state, as a database ``UPDATE ... WHERE`` does, and a job changed by another transaction since this
        one read it is judged by that newer state.

        :param entity: Job with its new state.
        :type entity: Job
        :param expected: States the stored job must be in for the replacement to happen.
        :type expected: Collection[JobState]
        :returns: The job as stored, or None when its stored state is not one of ``expected`` and nothing changed.
        :rtype: Job | None
        :raises NotFoundError: If the job is not stored.
        """


class BookPlaceRepository(Repository[BookPlace, BookPlaceKey]):
    """The place each account left each book at; deleting a book or an account removes its places."""

    @abstractmethod
    async def save(self, place: BookPlace) -> BookPlace:
        """Store the place of an account in a book, replacing the one stored whole, unless that one is newer.

        The write is atomic: of two transactions saving at once, the one with the later ``updated_at`` leaves its place
        whole, and no field of the other is kept. A place whose ``updated_at`` is earlier than the stored one changes
        nothing, so a request that arrives late never overwrites a newer one. The comparison sees the place as last
        committed by anyone, not as this transaction read it.

        :param place: Place to store.
        :type place: BookPlace
        :returns: The place as stored, which is the stored place when it is newer than ``place``.
        :rtype: BookPlace
        :raises NotFoundError: If the book is not stored.
        """

    @abstractmethod
    async def find(self, key: BookPlaceKey) -> BookPlace | None:
        """Return the place of an account in a book.

        :param key: Account and book.
        :type key: BookPlaceKey
        :returns: The place, or None when the account has not worked on the book yet.
        :rtype: BookPlace | None
        """


class UnitOfWork(ABC):
    """One transaction over every repository; nothing is visible to others before it commits.

    Every write goes through a ``change_book`` or a ``change`` block, which commits when it ends normally. ``commit``
    and ``rollback`` remain only until every caller has moved to the blocks, and a later change removes them.

    :ivar projects: Project repository of this transaction.
    :ivar sources: Source repository of this transaction.
    :ivar scans: Scan repository of this transaction.
    :ivar pages: Page repository of this transaction.
    :ivar pagination_sections: Repository of the pagination sections of the books, of this transaction.
    :ivar page_versions: Page version repository of this transaction.
    :ivar page_stages: Page stage repository of this transaction.
    :ivar page_step_states: Repository of the settings and manual edits of the steps of the pages, of this transaction.
    :ivar page_step_changes: Repository of the history of the steps of the pages, of this transaction.
    :ivar step_values: Repository of the values of the steps for the odd pages, the even pages and the groups, of this
                       transaction.
    :ivar result_mark_changes: Repository of the log of the marks and comments of the results, of this transaction.
    :ivar recipes: Recipe repository of this transaction.
    :ivar recipe_profiles: Repository of the recipe profiles of the accounts, of this transaction.
    :ivar jobs: Job repository of this transaction.
    :ivar book_places: Repository of the places accounts left books at, of this transaction.
    """

    projects: ProjectRepository
    sources: SourceRepository
    scans: ScanRepository
    pages: PageRepository
    pagination_sections: PaginationSectionRepository
    page_versions: PageVersionRepository
    page_stages: PageStageRepository
    page_step_states: PageStepStateRepository
    page_step_changes: PageStepChangeRepository
    step_values: StepValuesRepository
    result_mark_changes: ResultMarkChangeRepository
    recipes: RecipeRepository
    recipe_profiles: RecipeProfileRepository
    jobs: JobRepository
    book_places: BookPlaceRepository

    @abstractmethod
    def change_book(self, project_id: ProjectId) -> AbstractAsyncContextManager[Project]:
        """Open a block that changes the book of a project, alone among the changes of that book.

        Entering waits for any other ``change_book`` of the same project to exit, then reads and locks the project, so
        a change that began earlier has committed or rolled back by then. Reads inside the block see everything
        committed before entry plus the block's own writes. Leaving the block normally commits it, and leaving it by
        an exception rolls it back and lets the exception through. Reads outside any block take no lock and wait for
        nothing. The port promises no ordering between the changes of different books, nor between a ``change_book``
        and a ``change`` block, though a database with one write lock, such as SQLite, makes every block wait for any
        other that writes.

        :param project_id: Project whose book is changed.
        :type project_id: ProjectId
        :returns: Context manager yielding the project as it stands at entry.
        :rtype: AbstractAsyncContextManager[Project]
        :raises NotFoundError: On entry, if the project is not stored.
        :raises BookBusyError: On entry, if another change of the book did not end within the wait limit of the adapter.
        :raises NestedChangeError: On entry, if a block of this unit of work is open already.
        """

    @abstractmethod
    def change(self) -> AbstractAsyncContextManager[None]:
        """Open a block that writes outside the content of a book, alone among the blocks of its kind.

        The block is for the rows that belong to no book or to many: jobs, the places accounts left books at, a new
        project, and the recipe profiles of an account. Entering waits for any other ``change`` block to exit.
        Leaving the block normally commits it, and leaving it by an exception rolls it back and lets the exception
        through. Reads inside the block see everything committed before entry plus the block's own writes.

        :returns: Context manager yielding nothing.
        :rtype: AbstractAsyncContextManager[None]
        :raises BookBusyError: On entry, if another block did not end within the wait limit of the adapter.
        :raises NestedChangeError: On entry, if a block of this unit of work is open already.
        """

    @abstractmethod
    async def commit(self) -> None:
        """Make every change since the last commit durable and visible.

        A job written by ``JobRepository.update_if_state`` keeps its guarded state until this commit: a database holds
        the row locked, so another writer waits, and an adapter without locks refuses the commit instead when another
        transaction changed that job in the meantime.

        :raises ConflictError: If a job this transaction wrote by ``update_if_state`` was changed and committed by
                               another transaction since; nothing of this transaction is kept then.
        """

    @abstractmethod
    async def rollback(self) -> None:
        """Discard every change since the last commit."""
