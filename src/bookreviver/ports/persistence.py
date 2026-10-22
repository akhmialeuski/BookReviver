"""Persistence ports: repositories per aggregate and the unit of work that runs a use case's work on them.

The data lives in two kinds of store. The catalogue is one for the application and holds what belongs to the
accounts: the library, which says which account owns which book, and the recipe profiles. Each book has a store of its
own that holds everything of the book and nothing of any account, so a book moves between instances as it is. Nothing
refers across two stores by a key the store checks, and every list of books goes through the library.

Services never see a database. They hand the ``UnitOfWork`` a function over the repositories of one store, and the unit
of work runs that function whole on one transaction of that store: ``read`` and ``change`` over the
``CatalogueRepositories``, ``read_book`` and ``change_book`` over the ``BookRepositories`` of one book. A read only
reads, and a change commits when the function returns. The repositories are synchronous, because the function runs
from its first statement to its commit without returning to the event loop, which is what keeps a write short: the
adapter of a database runs it in a thread of its own.

The book is the aggregate. Every change of a book runs in ``change_book``, which waits for any other change of that
book and reads the project first, so it sees only what the previous change committed, and never for a change of
another book. A repository write inside a read is a programming error, reported as ``NoChangeOpenError``. The function
cannot open another transaction, because it holds repositories and not the unit of work, so changes never nest, and a
use case that needs both stores runs one transaction of each.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, NewType

from bookreviver.domain.entities import (
    LibraryEntry,
    Page,
    PageHistoryEntry,
    PageStage,
    PageStepState,
    PageVersion,
    PaginationSection,
    Project,
    Recipe,
    RecipeProfile,
    Scan,
    Source,
)
from bookreviver.domain.ids import (
    PageHistoryEntryId,
    PageId,
    PageVersionId,
    PaginationSectionId,
    ProjectId,
    RecipeId,
    RecipeProfileId,
    ScanId,
    SourceId,
)
from bookreviver.domain.step_values import StepValues, StepValuesKey
from bookreviver.domain.values import PageStageKey, PageStepKey

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping, Sequence
    from datetime import datetime

    from bookreviver.domain.entities import (
        BookPlace,
        ProjectOverview,
    )
    from bookreviver.domain.enums import RecipeKind, ResultMark, Side, Stage, VersionScale
    from bookreviver.domain.ids import AccountId, ChangeBatchId, StepId
    from bookreviver.domain.stage_summaries import StageTally, StepTally
    from bookreviver.domain.values import PageSize, ProcessorRef, Slice, SliceRequest

# How long, in seconds, a change waits for another change of the same book before it gives up with ``BookBusyError``
DEFAULT_CHANGE_WAIT_SECONDS: float = 30.0
# The dependency key of the limit, in seconds, that one unit of work waits for a change with before it gives up with
# ``BookBusyError``. The container chooses it per scope, a shorter one for a unit of work that serves an HTTP request
# than for one of a job, and hands it to the adapter that builds the unit of work
ChangeWaitSeconds = NewType('ChangeWaitSeconds', float)


class NoChangeOpenError(RuntimeError):
    """A repository wrote inside the work of ``UnitOfWork.read``.

    It is a programming error and not a ``DomainError``, so it reaches the caller as a failure and no request can
    cause it.
    """

    def __init__(self) -> None:
        """Report the write with its fixed sentence."""
        super().__init__('A repository write needs change_book or change, and read only reads.')


class Repository[EntityT, IdT](ABC):
    """Storage of one kind of entity addressed by its identifier."""

    @abstractmethod
    def get(self, entity_id: IdT) -> EntityT:
        """Return the entity.

        :param entity_id: Identifier of the entity.
        :type entity_id: IdT
        :returns: The stored entity.
        :rtype: EntityT
        :raises NotFoundError: If no entity has this identifier.
        """

    @abstractmethod
    def add(self, entity: EntityT) -> EntityT:
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
    def add_many(self, entities: Sequence[EntityT]) -> Sequence[EntityT]:
        """Store several new entities at once, all of them or, on an error, none.

        After an error the change that holds the write rolls back when its work raises the exception, as a database
        requires.

        :param entities: Entities to store, with their identifiers already assigned.
        :type entities: Sequence[EntityT]
        :returns: The entities as stored, in the given order.
        :rtype: Sequence[EntityT]
        :raises ConflictError: If an identifier or a unique value of one entity is stored already or given twice.
        :raises NotFoundError: If an entity one of them refers to is not stored.
        """

    @abstractmethod
    def update(self, entity: EntityT) -> EntityT:
        """Replace the stored state of an existing entity and return it as stored.

        :param entity: Entity with its new state.
        :type entity: EntityT
        :returns: The entity as stored.
        :rtype: EntityT
        :raises NotFoundError: If the entity, or an entity it refers to, is not stored.
        :raises ConflictError: If its new state takes a value its port declares unique from another entity.
        """

    @abstractmethod
    def delete(self, entity_id: IdT) -> None:
        """Remove the entity, together with everything that belongs to it.

        :param entity_id: Identifier of the entity.
        :type entity_id: IdT
        :raises NotFoundError: If no entity has this identifier.
        """


class KeyedRepository[EntityT, KeyT](Repository[EntityT, KeyT]):
    """Storage of entities addressed by a key of several values, written by replacing the entity stored under the key.

    Such an entity has no identifier of its own, so ``save`` and ``save_many`` store it whether or not the key is taken,
    where ``add`` refuses a taken key and ``update`` a free one.
    """

    def save(self, entity: EntityT) -> EntityT:
        """Store the entity, replacing the one stored under the same key.

        :param entity: Entity to store.
        :type entity: EntityT
        :returns: The entity as stored.
        :rtype: EntityT
        :raises NotFoundError: If an entity it refers to, such as its page, is not stored.
        """
        [stored] = self.save_many([entity])
        return stored

    @abstractmethod
    def save_many(self, entities: Sequence[EntityT]) -> Sequence[EntityT]:
        """Store several entities at once, each replacing the one under its key, all of them or, on an error, none.

        After an error the change that holds the write rolls back when its work raises the exception, as a database
        requires.

        :param entities: Entities to store.
        :type entities: Sequence[EntityT]
        :returns: The entities as stored, in the given order.
        :rtype: Sequence[EntityT]
        :raises NotFoundError: If an entity one of them refers to, such as its page, is not stored.
        """

    @abstractmethod
    def find(self, key: KeyT) -> EntityT | None:
        """Return the entity stored under the key.

        :param key: Key of the entity.
        :type key: KeyT
        :returns: The entity, or None when nothing is stored under the key.
        :rtype: EntityT | None
        """


class LibraryRepository(Repository[LibraryEntry, ProjectId]):
    """The library of the catalogue: which account owns which book, one entry for each book.

    ``add`` raises ``NotFoundError`` when the owner is not an account, and an account is not deleted while an entry
    names it.
    """

    @abstractmethod
    def list_for_owner(self, owner_id: AccountId, request: SliceRequest) -> Slice[LibraryEntry]:
        """Return the owner's books, the latest added first, ties by identifier.

        :param owner_id: Account owning the books.
        :type owner_id: AccountId
        :param request: Offset and limit of the window to return.
        :type request: SliceRequest
        :returns: The entries of the window and the number of all the owner's books.
        :rtype: Slice[LibraryEntry]
        """

    @abstractmethod
    def list_all(self) -> Sequence[LibraryEntry]:
        """Return the entry of every book of every account, for the passes of the start over all books.

        :returns: Every entry, by identifier.
        :rtype: Sequence[LibraryEntry]
        """


class ProjectRepository(Repository[Project, ProjectId]):
    """The project of a book, the one row its store has."""

    @abstractmethod
    def overview(self, project: Project) -> ProjectOverview:
        """Return a project read earlier together with the counts of its book.

        :param project: Project whose book is counted.
        :type project: Project
        :returns: The project with the counts of its included pages, sources and scans.
        :rtype: ProjectOverview
        """


class SourceRepository(Repository[Source, SourceId]):
    """Sources of the book; the digest of the main file of an imported source is unique.

    A rejected or skipped file is a source row too, and its digest may repeat, since it is no part of the book. Only
    the imported sources count as the project's sources, and the rejected and skipped ones are listed and deleted
    apart, when the reader closes the report of an import.

    Deleting a source removes its scans, and the pages of the book made from them stay without their scan.
    """

    @abstractmethod
    def list_all(self) -> Sequence[Source]:
        """Return the project's imported sources in the order they were imported, ties by identifier.

        :returns: Every imported source of the project, the earliest import first.
        :rtype: Sequence[Source]
        """

    @abstractmethod
    def list_unimported(self) -> Sequence[Source]:
        """Return the project's rejected and skipped files in the order they were recorded, ties by identifier.

        :returns: Every rejected or skipped source of the project, the earliest first.
        :rtype: Sequence[Source]
        """

    @abstractmethod
    def delete_unimported(self) -> None:
        """Delete the project's rejected and skipped sources, which hold no scan, and keep the imported ones."""

    @abstractmethod
    def find_by_sha256(self, sha256: str) -> Source | None:
        """Return the project's imported source whose main file has this digest, which refuses a second upload.

        :param sha256: SHA-256 digest of a main file as lower-case hexadecimal digits.
        :type sha256: str
        :returns: The imported source with this digest, or None when the project has none.
        :rtype: Source | None
        """


class ScanRepository(Repository[Scan, ScanId]):
    """Scans of the sources; the pair of a source and the number of a scan in it is unique."""

    @abstractmethod
    def list_for_source(self, source_id: SourceId) -> Sequence[Scan]:
        """Return the scans of one source in their order in the source.

        :param source_id: Source holding the scans.
        :type source_id: SourceId
        :returns: Every scan of the source, by number.
        :rtype: Sequence[Scan]
        """

    @abstractmethod
    def list_window(self, request: SliceRequest) -> Slice[Scan]:
        """Return the scans of a project, source by source in import order and by number within a source.

        :param request: Offset and limit of the window to return.
        :type request: SliceRequest
        :returns: The scans of the window and the number of all the project's scans.
        :rtype: Slice[Scan]
        """

    @abstractmethod
    def list_by_ids(self, scan_ids: Collection[ScanId]) -> Sequence[Scan]:
        """Return several scans in one read, so a window of the book costs one query to learn its sources.

        :param scan_ids: Scans to read; one that is not stored is left out of the result.
        :type scan_ids: Collection[ScanId]
        :returns: The stored scans among them, in no particular order.
        :rtype: Sequence[Scan]
        """

    @abstractmethod
    def list_unready(self) -> Sequence[Scan]:
        """Return the scans of a project whose renditions are not ready, which an import cut again.

        :returns: The scans without ready renditions, source by source in import order and by number within a source.
        :rtype: Sequence[Scan]
        """


class PageRepository(Repository[Page, PageId]):
    """Pages of the book, addressed by their identifier and ordered by their order key.

    An order key is unique, and a part of a scan, the pair of a scan and a slot, belongs to one page at most. A page
    keeps its row when its scan is deleted, and loses only the reference to it. Deleting a page removes its versions.
    """

    @abstractmethod
    def list_window(self, request: SliceRequest, *, included_only: bool = False) -> Slice[Page]:
        """Return the pages of a project in book order, the byte order of their order keys.

        The position of a page in the book is the offset of the window plus its index in the window.

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
    def list_by_ids(self, page_ids: Collection[PageId]) -> Sequence[Page]:
        """Return the given pages of a project in book order.

        :param page_ids: Identifiers of the pages to read, repeats counting once.
        :type page_ids: Collection[PageId]
        :returns: The pages, in the byte order of their order keys.
        :rtype: Sequence[Page]
        :raises NotFoundError: If an identifier names no page.
        """

    @abstractmethod
    def list_for_source(self, source_id: SourceId) -> Sequence[Page]:
        """Return the pages whose scans belong to a source, in book order.

        Pages without a scan, such as placeholders and blank leaves, belong to no source and are never listed.

        :param source_id: Source whose scans the pages show.
        :type source_id: SourceId
        :returns: The pages cut from the scans of the source, by order key.
        :rtype: Sequence[Page]
        """

    @abstractmethod
    def neighbour_key(self, key: str, side: Side, *, excluding: Collection[PageId] = ()) -> str | None:
        """Return the order key of the page next to ``key`` on one side, leaving some pages out of the count.

        A group of pages that is about to move is left out, so the key found is the one of the page the group will
        stand next to once it has left its old place.

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
    def update_many(self, pages: Sequence[Page]) -> None:
        """Replace the stored state of several pages in the one transaction, all of them or, on an error, none.

        After an error the change that holds the write rolls back when its work raises the exception, as a database
        requires.

        :param pages: Pages with their new state.
        :type pages: Sequence[Page]
        :raises NotFoundError: If a page is not stored.
        :raises ConflictError: If the new state of a page takes an order key, or a part of a scan, that another page
                               of the project has.
        """

    @abstractmethod
    def list_for_scan(self, scan_id: ScanId) -> Sequence[Page]:
        """Return the pages cut from one scan, by their slot.

        :param scan_id: Scan the pages were cut from.
        :type scan_id: ScanId
        :returns: Every page that names the scan, the whole scan or its parts in slot order.
        :rtype: Sequence[Page]
        """

    @abstractmethod
    def count_before(self, page: Page) -> int:
        """Return how many pages of the page's project come before it in book order, which is its position.

        Every page counts, whether it is included in the book or not, as in the windows ``list_for_project`` returns.

        :param page: Stored page of the project.
        :type page: Page
        :returns: The number of the project's pages whose order key is smaller than the page's, from zero.
        :rtype: int
        """

    @abstractmethod
    def last_order_key(self) -> str | None:
        """Return the order key of the last page of a project, after which new pages are appended.

        :returns: The greatest order key of the project's pages, or None for a book without pages.
        :rtype: str | None
        """

    @abstractmethod
    def kind_tally(self) -> Mapping[RecipeKind, int]:
        """Count the pages with an image of each kind of a project, in one grouped query.

        The kind of a page is ``Page.recipe_kind``. A placeholder has no image and is never counted.

        :returns: The number of pages of each kind that has any page.
        :rtype: Mapping[RecipeKind, int]
        """


class PaginationSectionRepository(Repository[PaginationSection, PaginationSectionId]):
    """The pagination sections of the books, which a page of the book is numbered from.

    A section names the page it starts at. Deleting that page, or the project, removes the section with it, so a use
    case that deletes pages hands the section to the next page first.
    """

    @abstractmethod
    def list_all(self) -> Sequence[PaginationSection]:
        """Return the sections of a project in the order they were made, ties by identifier.

        The sections are not in book order, since the order of the book is the order of the pages they start at.

        :returns: Every section of the project.
        :rtype: Sequence[PaginationSection]
        """


class PageVersionRepository(Repository[PageVersion, PageVersionId]):
    """Versions of the pages of the book; a version whose input version is deleted keeps its row without the input."""

    @abstractmethod
    def list_for_page(self, page_id: PageId) -> Sequence[PageVersion]:
        """Return the versions of one page in the order they were created, ties by identifier.

        :param page_id: Page owning the versions.
        :type page_id: PageId
        :returns: Every version of the page, the earliest first.
        :rtype: Sequence[PageVersion]
        """

    @abstractmethod
    def list_to_prepare(self, processor_keys: Collection[str]) -> Sequence[PageVersion]:
        """Return the versions of a project whose files still have to be written: the pending and the failed ones.

        A failed version is returned with the pending ones, so the next job of the project tries it again and no route
        of its own is needed to repeat it.

        :param processor_keys: Keys of the processors whose versions are returned, those the job knows how to make.
        :type processor_keys: Collection[str]
        :returns: The pending and failed versions made by those processors, the earliest first, ties by identifier.
        :rtype: Sequence[PageVersion]
        """

    @abstractmethod
    def base_sizes(self) -> Sequence[PageSize]:
        """Return the sizes of the page split's base versions of the included pages that show a scan.

        The median of these is the size of a generated blank leaf. A leaf drawn in place of a scan is a version of the
        page order, so it is not counted. A version that records no size is left out.

        :returns: The recorded size of every such base version, in no particular order.
        :rtype: Sequence[PageSize]
        """

    @abstractmethod
    def list_base_versions(self, page_ids: Collection[PageId]) -> Sequence[PageVersion]:
        """Return the base versions of several pages in one read, so a window of a book costs one query.

        A base version is a version without an input version, the one a scan or nothing feeds. A page cut from a scan
        again gets a new base version beside the old one, so a page can have several.

        :param page_ids: Pages whose base versions are read.
        :type page_ids: Collection[PageId]
        :returns: The base versions of those pages, the earliest first, ties by identifier.
        :rtype: Sequence[PageVersion]
        """

    @abstractmethod
    def list_by_ids(self, version_ids: Collection[PageVersionId]) -> Sequence[PageVersion]:
        """Return the stored versions among the given identifiers, in one read.

        :param version_ids: Identifiers of the versions to read, of which those that are not stored are left out.
        :type version_ids: Collection[PageVersionId]
        :returns: The versions found, the earliest first, ties by identifier.
        :rtype: Sequence[PageVersion]
        """

    @abstractmethod
    def find(self, version_id: PageVersionId) -> PageVersion | None:
        """Return the version with this identifier, which a repeated run reuses instead of computing it again.

        :param version_id: Identifier of the version, the hash of what produces it.
        :type version_id: PageVersionId
        :returns: The stored version, ready or failed or still running, or None when no run has made it.
        :rtype: PageVersion | None
        """

    @abstractmethod
    def list_for_stage(
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
    def collectable(self, previews_older_than: datetime) -> Sequence[PageVersion]:
        """Return the versions a collection may delete: not base, and read by no version that stays.

        A version stays when a stage record names it as its head, when it is a base version, which has no input, when
        it is a preview too young to go, when the user marked it Good or wrote a comment on it, and when a version that
        stays reads it, directly or through the chain of inputs. Deleting an input would leave the version that reads
        it with no input, which the database allows and which would make it look like a base version for ever. A full
        run goes as soon as it is none of these, with no age to wait for, and a preview once it was created before
        ``previews_older_than``, since the editor that asked for it is still showing it.

        :param previews_older_than: Previews created before this moment may go.
        :type previews_older_than: datetime
        :returns: The versions that may be deleted, the earliest first, ties by identifier.
        :rtype: Sequence[PageVersion]
        """

    @abstractmethod
    def delete_many(self, version_ids: Collection[PageVersionId]) -> None:
        """Remove the rows of several versions in the one transaction; a version that is not stored is left alone.

        :param version_ids: Versions to remove.
        :type version_ids: Collection[PageVersionId]
        """


class PageStageRepository(KeyedRepository[PageStage, PageStageKey]):
    """The current version of each stage of each page, addressed by the page and the stage.

    Deleting a page removes its records. A record keeps its row when its head version or its recipe is deleted, and
    loses only that reference. Storing a record raises ``NotFoundError`` when its page, its head version or its recipe
    is not stored, and ``find`` returns None for a stage that has not run on the page.
    """

    @abstractmethod
    def list_for_page(self, page_id: PageId) -> Sequence[PageStage]:
        """Return the records of one page in the order of the stages.

        :param page_id: Page owning the records.
        :type page_id: PageId
        :returns: Every record of the page.
        :rtype: Sequence[PageStage]
        """

    @abstractmethod
    def list_for_pages(self, page_ids: Collection[PageId]) -> Sequence[PageStage]:
        """Return the records of several pages in one read, by page and then in the order of the stages.

        :param page_ids: Pages whose records are read.
        :type page_ids: Collection[PageId]
        :returns: Every record of those pages.
        :rtype: Sequence[PageStage]
        """

    @abstractmethod
    def list_for_recipe(self, recipe_id: RecipeId) -> Sequence[PageStage]:
        """Return the records of the pages a recipe processed, by page identifier.

        :param recipe_id: Recipe whose pages are listed.
        :type recipe_id: RecipeId
        :returns: Every record that names the recipe.
        :rtype: Sequence[PageStage]
        """

    @abstractmethod
    def list_fresh(self) -> Sequence[PageStage]:
        """Return the records of the pages of the book whose current version is up to date.

        :returns: The records in the fresh state, by page identifier and then in the order of the stages.
        :rtype: Sequence[PageStage]
        """

    @abstractmethod
    def list_replaced(self, stage: Stage, processor: ProcessorRef) -> Sequence[PageStage]:
        """Return the records of a stage of the book whose current version a replaced version of a processor made.

        :param stage: The stage whose records are read.
        :type stage: Stage
        :param processor: The processor by key and installed version: a record is returned when its current version was
                          made by the same key in another version, whatever the state of the record.
        :type processor: ProcessorRef
        :returns: The records, by page identifier.
        :rtype: Sequence[PageStage]
        """

    @abstractmethod
    def list_for_stage(self, stage: Stage) -> Sequence[PageStage]:
        """Return the records of one stage over the pages of a project, by page identifier.

        :param stage: The stage.
        :type stage: Stage
        :returns: Every record of the stage in the project.
        :rtype: Sequence[PageStage]
        """

    @abstractmethod
    def step_tally(self) -> Sequence[StepTally]:
        """Count the pages of every stage of a project that a run stopped at each step, in one grouped query.

        Only pages with an image are counted, and a record that failed is left out, since its step belongs to a run that
        did not end. A page run through every step that is on has no step and is not counted.

        :returns: One tally for each step of a stage that a page stopped at, in no particular order.
        :rtype: Sequence[StepTally]
        """

    @abstractmethod
    def head_ids(self) -> Collection[PageVersionId]:
        """Return the identifiers of the versions that are the current version of a stage of a page of the project.

        :returns: The distinct head versions.
        :rtype: Collection[PageVersionId]
        """

    @abstractmethod
    def tally(self) -> Sequence[StageTally]:
        """Count the records of every stage of the project by state, in one grouped query.

        Only pages with an image are counted, so a placeholder never is. A page counts as marked for review when the
        current version of its record carries a review mark and the record is not failed. A page counts toward check
        when its record is stale or failed or the page is marked, once however many of these hold. A stage that no page
        has a record of has no tally.

        :returns: One tally for each stage that has a record, in no particular order.
        :rtype: Sequence[StageTally]
        """


class PageStepStateRepository(KeyedRepository[PageStepState, PageStepKey]):
    """The settings and the manual edit of each step on each page, addressed by the page, the stage and the step.

    Deleting a page removes its states. Storing a state raises ``NotFoundError`` when its page is not stored, and
    ``find`` returns None when the page has neither a setting nor an edit for the step.
    """

    @abstractmethod
    def list_for_page(self, page_id: PageId, stage: Stage | None = None) -> Sequence[PageStepState]:
        """Return the states of one page, those of one stage or of all, by stage and step.

        :param page_id: Page owning the states.
        :type page_id: PageId
        :param stage: Stage whose states are listed, or None for every stage.
        :type stage: Stage | None
        :returns: The states of the page.
        :rtype: Sequence[PageStepState]
        """

    @abstractmethod
    def list_for_step(self, page_ids: Collection[PageId], stage: Stage, step_id: StepId) -> Sequence[PageStepState]:
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


class StepValuesRepository(KeyedRepository[StepValues, StepValuesKey]):
    """The values of the steps for the odd pages, the even pages and the groups, addressed by project, step and part.

    Deleting a project removes them. Storing values raises ``NotFoundError`` when the project is not stored, and
    ``find`` returns None when the part of the pages changes no field of the step.
    """

    @abstractmethod
    def list_for_step(self, step_id: StepId) -> Sequence[StepValues]:
        """Return the values every part of the pages of a project has for one step, by scope and group.

        Books built from one profile share the identifiers of the steps, so the values are those of one project.

        :param step_id: The step of a recipe.
        :type step_id: StepId
        :returns: The values of the step, which are none when no part changes a field of it.
        :rtype: Sequence[StepValues]
        """

    @abstractmethod
    def list_all(self) -> Sequence[StepValues]:
        """Return the values of every step of a project, by step, scope and group, which is one read for all stages.

        :returns: The values of the project, each naming its stage.
        :rtype: Sequence[StepValues]
        """


class PageHistoryRepository(Repository[PageHistoryEntry, PageHistoryEntryId]):
    """The history of the layers of the steps of each page. A change is added once and never rewritten.

    Deleting a page removes its history. ``add`` and ``add_many`` number the changes of a page from one in the order
    they are written, whatever their ``sequence``, and return them as stored.
    """

    @abstractmethod
    def list_for_page(self, page_id: PageId, stage: Stage | None = None) -> Sequence[PageHistoryEntry]:
        """Return the changes of one page, those of one stage or of all, in the order they were written (by sequence).

        :param page_id: Page the changes were made on.
        :type page_id: PageId
        :param stage: Stage whose changes are listed, or None for every stage.
        :type stage: Stage | None
        :returns: The changes of the page.
        :rtype: Sequence[PageHistoryEntry]
        """

    @abstractmethod
    def list_for_batch(self, batch_id: ChangeBatchId) -> Sequence[PageHistoryEntry]:
        """Return the changes of one batch, which may span several pages, by page and then by sequence.

        :param batch_id: Identifier the changes of the batch share.
        :type batch_id: ChangeBatchId
        :returns: The changes of the batch.
        :rtype: Sequence[PageHistoryEntry]
        """

    @abstractmethod
    def list_undoing(self, change_ids: Collection[PageHistoryEntryId]) -> Sequence[PageHistoryEntry]:
        """Return the changes that take back any of the given changes.

        :param change_ids: Identifiers of the changes that may have been undone.
        :type change_ids: Collection[PageHistoryEntryId]
        :returns: The undos, by page and then by sequence; none for changes that stand.
        :rtype: Sequence[PageHistoryEntry]
        """

    @abstractmethod
    def delete_for_step(self, key: PageStepKey) -> int:
        """Delete every change of one step on one page, whatever its layer, source or batch.

        Other steps and other pages keep their changes, and the next change added to the page is numbered after the
        highest sequence the page still holds.

        :param key: The page, the stage and the step.
        :type key: PageStepKey
        :returns: How many changes were deleted.
        :rtype: int
        """


class RecipeRepository(Repository[Recipe, RecipeId]):
    """Recipes of the book; a stage has at most one recipe for each kind, which the database keeps.

    Deleting a recipe leaves the page stages it processed without their recipe.
    """

    @abstractmethod
    def list_for_stage(self, stage: Stage) -> Sequence[Recipe]:
        """Return the recipes of one stage of a project, by creation, ties by identifier.

        :param stage: The stage.
        :type stage: Stage
        :returns: The recipes of the stage, one for each kind, or none before the stage has been used.
        :rtype: Sequence[Recipe]
        """

    @abstractmethod
    def list_all(self) -> Sequence[Recipe]:
        """Return the recipes of every stage of a project, in the order of the stages and then as ``list_for_stage``.

        :returns: The recipes of the stages that have been used.
        :rtype: Sequence[Recipe]
        """


class RecipeProfileRepository(Repository[RecipeProfile, RecipeProfileId]):
    """The recipe profiles of the accounts; an account has at most one default profile for each stage.

    Deleting an account removes its profiles. A recipe of a book may name the profile it was made from, but a book
    lives in a store of its own, so no key checks that name, and the service that deletes a profile empties it in the
    owner's books.
    """

    @abstractmethod
    def list_for_account(self, account_id: AccountId, stage: Stage | None = None) -> Sequence[RecipeProfile]:
        """Return the account's profiles in the order of the stages, then by creation, ties by identifier.

        :param account_id: Account owning the profiles.
        :type account_id: AccountId
        :param stage: The stage whose profiles are wanted, or None for every stage.
        :type stage: Stage | None
        :returns: The profiles of the account.
        :rtype: Sequence[RecipeProfile]
        """

    @abstractmethod
    def find_default(self, account_id: AccountId, stage: Stage) -> RecipeProfile | None:
        """Return the profile a new book of the account starts a stage with.

        :param account_id: Account owning the profile.
        :type account_id: AccountId
        :param stage: The stage.
        :type stage: Stage
        :returns: The default profile of the stage, or None when the account has not chosen one.
        :rtype: RecipeProfile | None
        """


class BookPlaceRepository(ABC):
    """The one place the reader left the book at, which goes with the file of the book."""

    @abstractmethod
    def save(self, place: BookPlace) -> BookPlace:
        """Store the place of the book, replacing the one stored whole, unless that one is newer.

        The write is atomic: of two transactions saving at once, the one with the later ``updated_at`` leaves its place
        whole, and no field of the other is kept. A place whose ``updated_at`` is earlier than the stored one changes
        nothing, so a request that arrives late never overwrites a newer one. The comparison sees the place as last
        committed by anyone, not as this transaction read it.

        :param place: Place to store.
        :type place: BookPlace
        :returns: The place as stored, which is the stored place when it is newer than ``place``.
        :rtype: BookPlace
        """

    @abstractmethod
    def find(self) -> BookPlace | None:
        """Return the place of the book.

        :returns: The place, or None when the book has not been worked on yet.
        :rtype: BookPlace | None
        """


class CatalogueRepositories(ABC):
    """Every repository of one transaction of the catalogue, which the work of ``read`` and ``change`` receives.

    :ivar library: Repository of the library, which account owns which book, of this transaction.
    :ivar recipe_profiles: Repository of the recipe profiles of the accounts, of this transaction.
    """

    library: LibraryRepository
    recipe_profiles: RecipeProfileRepository


class BookRepositories(ABC):
    """Every repository of one transaction of a book, which the work of ``read_book`` and ``change_book`` receives.

    :ivar projects: Repository of the project of the book, of this transaction.
    :ivar sources: Source repository of this transaction.
    :ivar scans: Scan repository of this transaction.
    :ivar pages: Page repository of this transaction.
    :ivar pagination_sections: Repository of the pagination sections of the book, of this transaction.
    :ivar page_versions: Page version repository of this transaction.
    :ivar page_stages: Page stage repository of this transaction.
    :ivar page_step_states: Repository of the settings and manual edits of the steps of the pages, of this transaction.
    :ivar page_history: Repository of the history of the steps of the pages, of this transaction.
    :ivar step_values: Repository of the values of the steps for the odd pages, the even pages and the groups, of this
                       transaction.
    :ivar recipes: Recipe repository of this transaction.
    :ivar book_places: Repository of the place the reader left the book at, of this transaction.
    """

    projects: ProjectRepository
    sources: SourceRepository
    scans: ScanRepository
    pages: PageRepository
    pagination_sections: PaginationSectionRepository
    page_versions: PageVersionRepository
    page_stages: PageStageRepository
    page_step_states: PageStepStateRepository
    page_history: PageHistoryRepository
    step_values: StepValuesRepository
    recipes: RecipeRepository
    book_places: BookPlaceRepository


class UnitOfWork(ABC):
    """Runs the work of a use case on the repositories of one store, one transaction for each call.

    The work is a synchronous function over the repositories of the catalogue or of one book, and the unit of work runs
    it whole: the transaction begins before its first statement and ends when it returns or raises, and nothing of it
    is visible to others before it commits. A change commits when the work returns and rolls back when it raises, and
    the exception goes on to the caller. Nothing holds a connection between two calls, so a caller may wait as long as
    it needs between them.

    A unit of work keeps no state between calls, so coroutines may share one and call it at once. Changes of one
    process that wait for the same store enter in the order they asked for it, so one that came early is never
    overtaken by one that came later, and each waits at most the wait limit of the unit of work before it gives up with
    ``BookBusyError``. Changes of different stores never wait for each other.
    """

    @abstractmethod
    async def read[T](self, work: Callable[[CatalogueRepositories], T]) -> T:
        """Run ``work`` on a transaction of the catalogue that only reads, and return what it returns.

        Every read of the work sees the same committed state of the catalogue. A write is refused with
        ``NoChangeOpenError``.

        :param work: Function over the repositories of the transaction.
        :type work: Callable[[CatalogueRepositories], T]
        :returns: What the work returns.
        :rtype: T
        :raises NoChangeOpenError: If the work writes.
        """

    @abstractmethod
    async def change[T](self, work: Callable[[CatalogueRepositories], T]) -> T:
        """Run ``work`` on a transaction of the catalogue that writes, commit it when the work returns, and return it.

        Reads of the work see everything committed before it began plus its own writes. When the work raises, the
        transaction rolls back and the exception goes on.

        :param work: Function over the repositories of the transaction.
        :type work: Callable[[CatalogueRepositories], T]
        :returns: What the work returns.
        :rtype: T
        :raises BookBusyError: If the write did not begin within the wait limit of the unit of work.
        """

    @abstractmethod
    async def read_book[T](self, project_id: ProjectId, work: Callable[[BookRepositories], T]) -> T:
        """Run ``work`` on a transaction of the book that only reads, and return what it returns.

        Every read of the work sees the same committed state of the book. A write is refused with
        ``NoChangeOpenError``.

        :param project_id: Project whose book is read.
        :type project_id: ProjectId
        :param work: Function over the repositories of the transaction.
        :type work: Callable[[BookRepositories], T]
        :returns: What the work returns.
        :rtype: T
        :raises NotFoundError: If the book is not stored.
        :raises NoChangeOpenError: If the work writes.
        """

    @abstractmethod
    async def change_book[T](self, project_id: ProjectId, work: Callable[[BookRepositories, Project], T]) -> T:
        """Run ``work`` on a transaction of the book that writes, alone among its changes, with the project as it is.

        The change waits for any other change of the same book to end, then reads the project, so it sees what the
        previous change committed. It never waits for a change of another book or of the catalogue.

        :param project_id: Project whose book is changed.
        :type project_id: ProjectId
        :param work: Function over the repositories of the transaction and the project read at its start.
        :type work: Callable[[BookRepositories, Project], T]
        :returns: What the work returns.
        :rtype: T
        :raises NotFoundError: If the book is not stored.
        :raises BookBusyError: If the write did not begin within the wait limit of the unit of work.
        """

    @abstractmethod
    async def create_book(self, project: Project) -> Project:
        """Make the store of a new book, holding its project and nothing else yet.

        :param project: The project of the new book.
        :type project: Project
        :returns: The project as stored.
        :rtype: Project
        :raises ConflictError: If a book with this identifier is stored.
        """

    @abstractmethod
    async def delete_book(self, project_id: ProjectId) -> None:
        """Remove the store of a book with everything in it, once its changes in progress have ended.

        A book that is not stored is left as it is, so a deletion broken off half way can run again.

        :param project_id: Project whose book is removed.
        :type project_id: ProjectId
        """

    @abstractmethod
    async def stored_books(self) -> Collection[ProjectId]:
        """Return the books that have a store, whether the library lists them or not.

        :returns: The identifiers of the stored books.
        :rtype: Collection[ProjectId]
        """
