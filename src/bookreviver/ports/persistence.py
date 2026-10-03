"""Persistence ports: repositories per aggregate and the unit of work that commits them together.

Services never see a database. They open a ``UnitOfWork``, read and change entities through its repositories, and
commit, so every change of one use case lands in one transaction. The in-memory and SQLAlchemy adapters both run the
contract suite in ``tests/contracts``, which is what makes them interchangeable.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, override

from bookreviver.domain.entities import (
    BookPlace,
    Job,
    Page,
    PageEdit,
    PageStage,
    PageVersion,
    Project,
    Recipe,
    RecipeProfile,
    RecipeRule,
    Scan,
    Source,
)
from bookreviver.domain.ids import (
    JobId,
    PageId,
    PageVersionId,
    ProjectId,
    RecipeId,
    RecipeProfileId,
    RecipeRuleId,
    ScanId,
    SourceId,
)
from bookreviver.domain.values import BookPlaceKey, PageEditKey, PageStageKey

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence
    from datetime import datetime

    from bookreviver.domain.entities import ProjectOverview
    from bookreviver.domain.enums import JobState, Side, Stage, VersionScale
    from bookreviver.domain.ids import AccountId
    from bookreviver.domain.stage_summaries import StageTally, VariantTally
    from bookreviver.domain.values import PageSize, Slice, SliceRequest


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
        """Return the sizes of the base versions of the project's pages that show a scan and are part of the book.

        The median of these is the size of a generated blank leaf. A version that records no size is left out.

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
        self, page_id: PageId, stage: Stage | None, scale: VersionScale | None, request: SliceRequest
    ) -> Slice[PageVersion]:
        """Return a window of the versions of one page, filtered by stage and scale, the earliest first.

        :param page_id: Page owning the versions.
        :type page_id: PageId
        :param stage: Stage whose versions are listed, or None for every stage.
        :type stage: Stage | None
        :param scale: Scale of the runs listed, or None for both.
        :type scale: VersionScale | None
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :returns: The versions of the window and the number of all that match, ties by identifier.
        :rtype: Slice[PageVersion]
        """

    @abstractmethod
    async def collectable(
        self, project_id: ProjectId, older_than: datetime, previews_older_than: datetime
    ) -> Sequence[PageVersion]:
        """Return the versions a collection may delete: old, not base, and read by no version that stays.

        A version stays when a stage record names it as its head, when it is a base version, which has no input, when
        it is too young to go, and when a version that stays reads it, directly or through the chain of inputs.
        Deleting an input would leave the version that reads it with no input, which the database allows and which
        would make it look like a base version for ever. A full run is old once it was created before ``older_than``,
        and a preview once it was created before ``previews_older_than``.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param older_than: Full runs created before this moment may go.
        :type older_than: datetime
        :param previews_older_than: Previews created before this moment may go.
        :type previews_older_than: datetime
        :returns: The versions that may be deleted, the earliest first, ties by identifier.
        :rtype: Sequence[PageVersion]
        """

    @abstractmethod
    async def delete_many(self, version_ids: Collection[PageVersionId]) -> None:
        """Remove the rows of several versions in the one transaction; a version that is not stored is left alone.

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
    async def variant_tally(self, project_id: ProjectId) -> Sequence[VariantTally]:
        """Count the pages each recipe processed, for every stage of a project, in one grouped query.

        Only pages with an image are counted, so a placeholder never is, and a record whose recipe was deleted names no
        recipe and is left out.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :returns: One tally for each recipe that processed a page, in no particular order.
        :rtype: Sequence[VariantTally]
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


class PageEditRepository(Repository[PageEdit, PageEditKey]):
    """Manual edits, one for each processor on each stage of each page; deleting a page removes its edits."""

    @abstractmethod
    async def save(self, edit: PageEdit) -> PageEdit:
        """Store an edit, replacing the one the same processor reads on the same page and stage.

        :param edit: Edit to store.
        :type edit: PageEdit
        :returns: The edit as stored.
        :rtype: PageEdit
        :raises NotFoundError: If the page is not stored.
        """

    @abstractmethod
    async def find(self, key: PageEditKey) -> PageEdit | None:
        """Return one edit.

        :param key: Page, stage and processor.
        :type key: PageEditKey
        :returns: The edit, or None when the user made none.
        :rtype: PageEdit | None
        """

    @abstractmethod
    async def list_for_page(self, page_id: PageId, stage: Stage | None = None) -> Sequence[PageEdit]:
        """Return the edits of one page, those of one stage or of all, by stage and processor.

        :param page_id: Page owning the edits.
        :type page_id: PageId
        :param stage: Stage whose edits are listed, or None for every stage.
        :type stage: Stage | None
        :returns: The edits of the page.
        :rtype: Sequence[PageEdit]
        """


class RecipeRepository(Repository[Recipe, RecipeId]):
    """Recipes of the projects; a stage of a project has at most one active recipe, which the database keeps.

    Deleting a recipe leaves the page stages it processed without their recipe.
    """

    @abstractmethod
    async def list_for_stage(self, project_id: ProjectId, stage: Stage) -> Sequence[Recipe]:
        """Return the recipes of one stage of a project, the active one first, then by creation, ties by identifier.

        :param project_id: Project owning the recipes.
        :type project_id: ProjectId
        :param stage: The stage.
        :type stage: Stage
        :returns: The active recipe and the variants of the stage.
        :rtype: Sequence[Recipe]
        """

    @abstractmethod
    async def find_active(self, project_id: ProjectId, stage: Stage) -> Recipe | None:
        """Return the active recipe of a stage of a project.

        :param project_id: Project owning the recipe.
        :type project_id: ProjectId
        :param stage: The stage.
        :type stage: Stage
        :returns: The active recipe, or None before the stage has been used.
        :rtype: Recipe | None
        """

    @abstractmethod
    async def list_active(self, project_id: ProjectId) -> Sequence[Recipe]:
        """Return the active recipe of every stage of a project that has one, in the order of the stages.

        :param project_id: Project owning the recipes.
        :type project_id: ProjectId
        :returns: The active recipes, one for each stage that has been used.
        :rtype: Sequence[Recipe]
        """


class RecipeRuleRepository(Repository[RecipeRule, RecipeRuleId]):
    """The rules that send pages to recipes of a stage; deleting a recipe or a project removes its rules."""

    @abstractmethod
    async def list_for_stage(self, project_id: ProjectId, stage: Stage) -> Sequence[RecipeRule]:
        """Return the rules of one stage of a project in the order they are tried, ties by identifier.

        :param project_id: Project owning the rules.
        :type project_id: ProjectId
        :param stage: The stage.
        :type stage: Stage
        :returns: The rules of the stage, the first to try first.
        :rtype: Sequence[RecipeRule]
        """


class RecipeProfileRepository(Repository[RecipeProfile, RecipeProfileId]):
    """The recipe profiles of the accounts; an account has at most one default profile for each stage.

    Deleting an account removes its profiles. Nothing in a book refers to a profile, so deleting one leaves the recipes
    made from it as they are.
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
        """Store the place of an account in a book, replacing the one stored.

        :param place: Place to store.
        :type place: BookPlace
        :returns: The place as stored.
        :rtype: BookPlace
        :raises NotFoundError: If the book is not stored.
        :raises ConflictError: If another transaction stored the first place of the account in the book meanwhile.
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
    """One transaction over every repository; nothing is visible to others before ``commit``.

    :ivar projects: Project repository of this transaction.
    :ivar sources: Source repository of this transaction.
    :ivar scans: Scan repository of this transaction.
    :ivar pages: Page repository of this transaction.
    :ivar page_versions: Page version repository of this transaction.
    :ivar page_stages: Page stage repository of this transaction.
    :ivar page_edits: Page edit repository of this transaction.
    :ivar recipes: Recipe repository of this transaction.
    :ivar recipe_rules: Repository of the rules that send pages to recipes, of this transaction.
    :ivar recipe_profiles: Repository of the recipe profiles of the accounts, of this transaction.
    :ivar jobs: Job repository of this transaction.
    :ivar book_places: Repository of the places accounts left books at, of this transaction.
    """

    projects: ProjectRepository
    sources: SourceRepository
    scans: ScanRepository
    pages: PageRepository
    page_versions: PageVersionRepository
    page_stages: PageStageRepository
    page_edits: PageEditRepository
    recipes: RecipeRepository
    recipe_rules: RecipeRuleRepository
    recipe_profiles: RecipeProfileRepository
    jobs: JobRepository
    book_places: BookPlaceRepository

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
