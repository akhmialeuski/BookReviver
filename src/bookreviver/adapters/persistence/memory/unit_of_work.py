"""In-memory unit of work: repositories work on a copy of the database, and ``commit`` publishes only the changes.

The adapter backs the service and API tests and runs the same port contract suite as the SQLAlchemy adapter, so it
reproduces the behaviour services rely on rather than only storing rows. It mirrors the schema of the SQL tables and
transaction isolation:

- Foreign keys: a source, a scan, a page, a recipe or a job needs its project, a scan its source, a version, a page
  stage, a page step state and a page step change their page, and a source, a page, a version or a page stage the
  import job, scan, input version, head version or recipe it names. A project's cover is one of its own pages. The
  owner of a project is not checked, because accounts belong to fastapi-users and have no port.
- Unique keys: the digest of a source's main file within its project, the number of a scan within its source, the
  order key of a page within its project, the pair of a scan and a slot, the project of a queued or running
  import job, so a project runs one import at a time, and the project and stage of an active recipe, so a stage has
  one active recipe.
- Referential actions: a project takes its sources, scans, pages, recipes and jobs with it, a source its scans, and a
  page its versions, stage records, step states and step changes. A deleted cover page leaves its project without a
  cover, a deleted scan leaves its pages without their scan, a deleted job leaves the sources it imported without
  their import job, a deleted version leaves the versions it fed without their input and the stage records it headed
  without their head, and a deleted recipe leaves the stage records it processed without their recipe.
- Isolation: a unit of work reads and writes a private copy of the tables, and ``commit`` merges only the rows it
  added, replaced or removed, so two units of work touching different rows do not overwrite each other.

Each repository states its table's keys in two hooks of the generic repository, ``_check`` before a row is stored and
``_cascade`` after one is removed, so the generic operations stay in one place.
"""

from collections import Counter
from functools import partial
from operator import attrgetter
from typing import TYPE_CHECKING, override

from attrs import define, evolve, field, fields

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
    ProjectOverview,
    Recipe,
    RecipeProfile,
    RecipeRule,
    Scan,
    Source,
)
from bookreviver.domain.enums import JobKind, JobState, PageOrigin, Side, Stage, StageState, VersionScale, VersionState
from bookreviver.domain.errors import ConcurrentChangeError, ConflictError, DomainError, NotFoundError
from bookreviver.domain.ids import (
    JobId,
    PageId,
    PageStepChangeId,
    PageVersionId,
    PaginationSectionId,
    ProjectId,
    RecipeId,
    RecipeProfileId,
    RecipeRuleId,
    ScanId,
    SourceId,
)
from bookreviver.domain.stage_summaries import StageTally, StepTally, VariantTally
from bookreviver.domain.values import BookPlaceKey, PageSize, PageStageKey, PageStepKey, Slice, SliceRequest
from bookreviver.domain.version_chains import collectable_versions
from bookreviver.ports.persistence import (
    BookPlaceRepository,
    JobRepository,
    PageRepository,
    PageStageRepository,
    PageStepChangeRepository,
    PageStepStateRepository,
    PageVersionRepository,
    PaginationSectionRepository,
    ProjectRepository,
    RecipeProfileRepository,
    RecipeRepository,
    RecipeRuleRepository,
    Repository,
    ScanRepository,
    SourceRepository,
    UnitOfWork,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Hashable, Iterable, Mapping, Sequence
    from datetime import datetime

    from bookreviver.domain.ids import AccountId, ChangeBatchId, StepId

# Attribute holding the identifier of every entity addressed by one
ID_ATTRIBUTE: str = 'id'
# Property holding the composite key of an entity that is addressed by one
KEY_ATTRIBUTE: str = 'key'
# The kinds and states of the jobs of which a project has at most one, each the rows of a partial unique index of the
# ``jobs`` table: the imports, the writing of page images, the jobs the user asks to process the versions of pages, the
# housekeeping jobs on them, and the one of all of these that is running
AT_MOST_ONE_JOB: tuple[tuple[frozenset[JobKind], frozenset[JobState]], ...] = (
    (frozenset({JobKind.IMPORT_SOURCE}), frozenset(JobState.active())),
    (frozenset({JobKind.PREPARE_PAGES}), frozenset(JobState.active())),
    (JobKind.requested(), frozenset(JobState.active())),
    (JobKind.housekeeping(), frozenset(JobState.active())),
    (JobKind.processing(), frozenset({JobState.RUNNING})),
)


@define(kw_only=True)
class InMemoryTables:
    """The rows of every table, keyed by identifier.

    :ivar projects: Projects by identifier.
    :ivar sources: Sources by identifier.
    :ivar scans: Scans by identifier.
    :ivar pages: Pages by identifier.
    :ivar pagination_sections: Pagination sections by identifier.
    :ivar page_versions: Page versions by identifier.
    :ivar page_stages: Page stage records by page and stage.
    :ivar page_step_states: Settings and manual edits of the steps by page, stage and step.
    :ivar page_step_changes: Changes of the layers of the steps of the pages by identifier.
    :ivar recipes: Recipes by identifier.
    :ivar recipe_rules: Rules of the stages by identifier.
    :ivar recipe_profiles: Recipe profiles of the accounts by identifier.
    :ivar jobs: Jobs by identifier.
    :ivar book_places: Places of books by account and book.
    """

    projects: dict[ProjectId, Project] = field(factory=dict)
    sources: dict[SourceId, Source] = field(factory=dict)
    scans: dict[ScanId, Scan] = field(factory=dict)
    pages: dict[PageId, Page] = field(factory=dict)
    pagination_sections: dict[PaginationSectionId, PaginationSection] = field(factory=dict)
    page_versions: dict[PageVersionId, PageVersion] = field(factory=dict)
    page_stages: dict[PageStageKey, PageStage] = field(factory=dict)
    page_step_states: dict[PageStepKey, PageStepState] = field(factory=dict)
    page_step_changes: dict[PageStepChangeId, PageStepChange] = field(factory=dict)
    recipes: dict[RecipeId, Recipe] = field(factory=dict)
    recipe_rules: dict[RecipeRuleId, RecipeRule] = field(factory=dict)
    recipe_profiles: dict[RecipeProfileId, RecipeProfile] = field(factory=dict)
    jobs: dict[JobId, Job] = field(factory=dict)
    book_places: dict[BookPlaceKey, BookPlace] = field(factory=dict)

    def forget_scans(self, scan_ids: Collection[ScanId]) -> None:
        """Leave the pages cut from these scans without their scan, as the database's ``SET NULL`` does.

        :param scan_ids: Scans just removed.
        :type scan_ids: Collection[ScanId]
        """
        for page in [page for page in self.pages.values() if page.scan_id in scan_ids]:
            self.pages[page.id] = evolve(page, scan_id=None)


@define
class InMemoryDatabase:
    """The committed state shared by every unit of work, like a database server.

    :ivar tables: Committed rows of every table.
    """

    tables: InMemoryTables = field(factory=InMemoryTables)


def require[KeyT](rows: Mapping[KeyT, object], key: KeyT | None) -> None:
    """Mirror a foreign key: a key that is given must be stored.

    :param rows: Table the foreign key refers to.
    :type rows: Mapping[KeyT, object]
    :param key: Value of the foreign key, or None for a nullable key left empty.
    :type key: KeyT | None
    :raises NotFoundError: If the key is given and not stored.
    """
    if key is not None and key not in rows:
        raise NotFoundError(key)


def remove_where[KeyT, RowT](rows: dict[KeyT, RowT], predicate: Callable[[RowT], bool]) -> None:
    """Remove every row matching ``predicate``, in place so every repository of the unit keeps seeing the same table.

    :param rows: Table to remove rows from.
    :type rows: dict[KeyT, RowT]
    :param predicate: Test selecting the rows to remove.
    :type predicate: Callable[[RowT], bool]
    """
    for key in [key for key, row in rows.items() if predicate(row)]:
        del rows[key]


class InMemoryRepository[EntityT, IdT](Repository[EntityT, IdT]):
    """Generic repository over one table of the working copy, with hooks for the keys of the table."""

    def __init__(self, rows: dict[IdT, EntityT], tables: InMemoryTables) -> None:
        """Work on one table of the unit of work's copy.

        :param rows: Table of the working copy, changed in place.
        :type rows: dict[IdT, EntityT]
        :param tables: Every table of the working copy, which the keys of this table refer to.
        :type tables: InMemoryTables
        """
        self._rows = rows
        self._tables = tables
        self._identify: Callable[[EntityT], IdT] = attrgetter(ID_ATTRIBUTE)

    @override
    async def get(self, entity_id: IdT) -> EntityT:
        """Return the entity with this identifier.

        :param entity_id: Identifier of the entity.
        :type entity_id: IdT
        :returns: The stored entity.
        :rtype: EntityT
        :raises NotFoundError: If no entity has this identifier.
        """
        if (entity := self._rows.get(entity_id)) is None:
            raise NotFoundError(entity_id)
        return entity

    @override
    async def add(self, entity: EntityT) -> EntityT:
        """Store a new entity.

        :param entity: Entity to store, with its identifier already assigned.
        :type entity: EntityT
        :returns: The entity as stored.
        :rtype: EntityT
        :raises ConflictError: If an entity with this identifier or one of its unique values is already stored.
        :raises NotFoundError: If an entity it refers to is not stored.
        """
        if (entity_id := self._identify(entity)) in self._rows:
            raise ConflictError(entity_id)
        self._check(entity)
        self._rows[entity_id] = entity
        return entity

    @override
    async def add_many(self, entities: Sequence[EntityT]) -> Sequence[EntityT]:
        """Store several new entities, all of them or none, as one database statement does.

        :param entities: Entities to store, with their identifiers already assigned.
        :type entities: Sequence[EntityT]
        :returns: The entities as stored, in the given order.
        :rtype: Sequence[EntityT]
        :raises ConflictError: If an identifier or a unique value of one entity is stored already or given twice.
        :raises NotFoundError: If an entity one of them refers to is not stored.
        """
        before = dict(self._rows)
        try:
            return [await self.add(entity) for entity in entities]
        except DomainError:
            self._rows.clear()
            self._rows.update(before)
            raise

    @override
    async def update(self, entity: EntityT) -> EntityT:
        """Replace the stored state of an existing entity.

        :param entity: Entity with its new state.
        :type entity: EntityT
        :returns: The entity as stored.
        :rtype: EntityT
        :raises NotFoundError: If the entity, or an entity it refers to, is not stored.
        :raises ConflictError: If its new state takes a unique value of another entity.
        """
        await self.get(entity_id := self._identify(entity))
        self._check(entity)
        self._rows[entity_id] = entity
        return entity

    @override
    async def delete(self, entity_id: IdT) -> None:
        """Remove the entity and apply the actions of the foreign keys referring to it.

        :param entity_id: Identifier of the entity.
        :type entity_id: IdT
        :raises NotFoundError: If no entity has this identifier.
        """
        entity = await self.get(entity_id)
        del self._rows[entity_id]
        self._cascade(entity)

    def _check(self, entity: EntityT) -> None:
        """Mirror the foreign keys and unique keys of the table for an entity about to be stored; none by default.

        :param entity: Entity about to be added or replaced.
        :type entity: EntityT
        """

    def _cascade(self, entity: EntityT) -> None:
        """Mirror the actions of the foreign keys referring to a removed entity; none by default.

        :param entity: Entity just removed.
        :type entity: EntityT
        """

    def _require_unique(self, entity: EntityT, value: Callable[[EntityT], Hashable]) -> None:
        """Mirror a unique key: no other entity of the table may have the same value.

        :param entity: Entity about to be stored.
        :type entity: EntityT
        :param value: Function returning the unique value of an entity, a tuple for a key of several columns.
        :type value: Callable[[EntityT], Hashable]
        :raises ConflictError: If another entity has the same value, named in the error.
        """
        taken, entity_id = value(entity), self._identify(entity)
        if any(value(row) == taken for key, row in self._rows.items() if key != entity_id):
            raise ConflictError(taken)


class InMemoryProjectRepository(InMemoryRepository[Project, ProjectId], ProjectRepository):
    """Projects with the counts of their books computed from the page, source and scan tables."""

    def __init__(self, tables: InMemoryTables) -> None:
        """Work on the project table of the unit of work's copy, reading the tables of its books as well.

        :param tables: Every table of the working copy.
        :type tables: InMemoryTables
        """
        super().__init__(tables.projects, tables)

    @override
    def _check(self, entity: Project) -> None:
        """Require the project's cover to be one of its own stored pages.

        :param entity: Project about to be stored.
        :type entity: Project
        :raises NotFoundError: If the cover page is not stored or belongs to another project.
        """
        if (cover_id := entity.cover_page_id) is None:
            return
        if (cover := self._tables.pages.get(cover_id)) is None or cover.project_id != entity.id:
            raise NotFoundError(cover_id)

    @override
    def _cascade(self, entity: Project) -> None:
        """Remove the project's sources, scans, pages with their records, recipes and jobs, as the cascade does.

        :param entity: Project just removed.
        :type entity: Project
        """
        doomed_pages = {page.id for page in self._tables.pages.values() if page.project_id == entity.id}
        remove_where(self._tables.page_versions, lambda version: version.page_id in doomed_pages)
        remove_where(self._tables.page_stages, lambda stage: stage.page_id in doomed_pages)
        remove_where(self._tables.page_step_states, lambda state: state.page_id in doomed_pages)
        remove_where(self._tables.page_step_changes, lambda change: change.page_id in doomed_pages)
        remove_where(self._tables.pagination_sections, lambda section: section.project_id == entity.id)
        remove_where(self._tables.pages, lambda page: page.id in doomed_pages)
        remove_where(self._tables.recipe_rules, lambda rule: rule.project_id == entity.id)
        remove_where(self._tables.recipes, lambda recipe: recipe.project_id == entity.id)
        remove_where(self._tables.sources, lambda source: source.project_id == entity.id)
        remove_where(self._tables.scans, lambda scan: scan.project_id == entity.id)
        remove_where(self._tables.jobs, lambda job: job.project_id == entity.id)
        remove_where(self._tables.book_places, lambda place: place.project_id == entity.id)

    @override
    async def overview(self, project: Project) -> ProjectOverview:
        """Count the included pages, the sources and the scans of the project.

        :param project: Project whose book is counted.
        :type project: Project
        :returns: The project with its counts.
        :rtype: ProjectOverview
        """
        tables = self._tables
        return ProjectOverview(
            project=project,
            page_count=sum(page.project_id == project.id and page.included for page in tables.pages.values()),
            source_count=sum(source.project_id == project.id for source in tables.sources.values()),
            scan_count=sum(scan.project_id == project.id for scan in tables.scans.values()),
            image_page_count=sum(
                page.project_id == project.id and page.origin is not PageOrigin.PLACEHOLDER
                for page in tables.pages.values()
            ),
        )

    @override
    async def list_for_owner(self, owner_id: AccountId, request: SliceRequest) -> Slice[ProjectOverview]:
        """Return one window of the owner's projects, most recently updated first, ties by identifier.

        :param owner_id: Account owning the projects.
        :type owner_id: AccountId
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :returns: The projects of the window with their counts, and the number of all the owner's projects.
        :rtype: Slice[ProjectOverview]
        """
        by_id = sorted(
            (project for project in self._tables.projects.values() if project.owner_id == owner_id),
            key=attrgetter(ID_ATTRIBUTE),
        )
        # A stable sort keeps the identifier order among projects updated at the same moment
        owned = sorted(by_id, key=attrgetter('updated_at'), reverse=True)
        window = owned[request.offset : request.offset + request.limit]
        return Slice(items=[await self.overview(project) for project in window], total=len(owned))


class InMemorySourceRepository(InMemoryRepository[Source, SourceId], SourceRepository):
    """Sources, unique by project and the digest of their main file."""

    def __init__(self, tables: InMemoryTables) -> None:
        """Work on the source table of the unit of work's copy, checking sources against projects and jobs.

        :param tables: Every table of the working copy.
        :type tables: InMemoryTables
        """
        super().__init__(tables.sources, tables)

    @override
    def _check(self, entity: Source) -> None:
        """Require the source's project and import job, and a digest new to the project.

        :param entity: Source about to be stored.
        :type entity: Source
        :raises NotFoundError: If the project, or the import job it names, is not stored.
        :raises ConflictError: If another source of the project has the same digest.
        """
        require(self._tables.projects, entity.project_id)
        require(self._tables.jobs, entity.import_job_id)
        self._require_unique(entity, attrgetter('project_id', 'sha256'))

    @override
    def _cascade(self, entity: Source) -> None:
        """Remove the source's scans and leave the pages cut from them without their scan, as the database does.

        :param entity: Source just removed.
        :type entity: Source
        """
        doomed_scans = {scan.id for scan in self._tables.scans.values() if scan.source_id == entity.id}
        remove_where(self._tables.scans, lambda scan: scan.id in doomed_scans)
        self._tables.forget_scans(doomed_scans)

    @override
    async def list_for_project(self, project_id: ProjectId) -> Sequence[Source]:
        """Return the project's sources, the earliest import first, ties by identifier.

        :param project_id: Project owning the sources.
        :type project_id: ProjectId
        :returns: Every source of the project in import order.
        :rtype: Sequence[Source]
        """
        return sorted(
            (source for source in self._rows.values() if source.project_id == project_id),
            key=attrgetter('imported_at', ID_ATTRIBUTE),
        )

    @override
    async def find_by_sha256(self, project_id: ProjectId, sha256: str) -> Source | None:
        """Return the project's source with this digest.

        :param project_id: Project owning the sources.
        :type project_id: ProjectId
        :param sha256: SHA-256 digest of a main file.
        :type sha256: str
        :returns: The source with this digest, or None.
        :rtype: Source | None
        """
        return next(
            (source for source in self._rows.values() if (source.project_id, source.sha256) == (project_id, sha256)),
            None,
        )


class InMemoryScanRepository(InMemoryRepository[Scan, ScanId], ScanRepository):
    """Scans, unique by source and number."""

    def __init__(self, tables: InMemoryTables) -> None:
        """Work on the scan table of the unit of work's copy, checking scans against projects and sources.

        :param tables: Every table of the working copy.
        :type tables: InMemoryTables
        """
        super().__init__(tables.scans, tables)

    @override
    def _check(self, entity: Scan) -> None:
        """Require the scan's project and source, and a number new to the source.

        :param entity: Scan about to be stored.
        :type entity: Scan
        :raises NotFoundError: If the project or the source is not stored.
        :raises ConflictError: If another scan of the source has the same number.
        """
        require(self._tables.projects, entity.project_id)
        require(self._tables.sources, entity.source_id)
        self._require_unique(entity, attrgetter('source_id', 'number'))

    @override
    def _cascade(self, entity: Scan) -> None:
        """Leave the pages cut from the scan without their scan, as the database's ``SET NULL`` does.

        :param entity: Scan just removed.
        :type entity: Scan
        """
        self._tables.forget_scans({entity.id})

    @override
    async def list_for_source(self, source_id: SourceId) -> Sequence[Scan]:
        """Return the scans of one source by number.

        :param source_id: Source holding the scans.
        :type source_id: SourceId
        :returns: Every scan of the source.
        :rtype: Sequence[Scan]
        """
        return sorted((scan for scan in self._rows.values() if scan.source_id == source_id), key=attrgetter('number'))

    @override
    async def list_for_project(self, project_id: ProjectId, request: SliceRequest) -> Slice[Scan]:
        """Return one window of the project's scans, source by source in import order and by number within one.

        :param project_id: Project owning the scans.
        :type project_id: ProjectId
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :returns: The scans of the window and the number of all the project's scans.
        :rtype: Slice[Scan]
        """
        sources = sorted(
            (source for source in self._tables.sources.values() if source.project_id == project_id),
            key=attrgetter('imported_at', ID_ATTRIBUTE),
        )
        rank = {source.id: position for position, source in enumerate(sources)}
        scans = sorted(
            (scan for scan in self._rows.values() if scan.project_id == project_id),
            key=lambda scan: (rank[scan.source_id], scan.number),
        )
        return Slice(items=scans[request.offset : request.offset + request.limit], total=len(scans))

    @override
    async def list_by_ids(self, scan_ids: Collection[ScanId]) -> Sequence[Scan]:
        """Return the stored scans among the given identifiers.

        :param scan_ids: Scans to read.
        :type scan_ids: Collection[ScanId]
        :returns: The scans that are stored.
        :rtype: Sequence[Scan]
        """
        return [scan for scan_id in set(scan_ids) if (scan := self._rows.get(scan_id)) is not None]

    @override
    async def list_unready(self, project_id: ProjectId) -> Sequence[Scan]:
        """Return the project's scans whose renditions are not ready, in the order ``list_for_project`` lists them.

        :param project_id: Project owning the scans.
        :type project_id: ProjectId
        :returns: The scans without ready renditions.
        :rtype: Sequence[Scan]
        """
        every_scan = await self.list_for_project(project_id, SliceRequest(limit=max(len(self._rows), 1)))
        return [scan for scan in every_scan.items if not scan.renditions.ready]


class InMemoryPageRepository(InMemoryRepository[Page, PageId], PageRepository):
    """Pages of the book, unique by project and order key and by scan and slot."""

    def __init__(self, tables: InMemoryTables, *, snapshot: InMemoryTables, committed: InMemoryTables) -> None:
        """Work on the page table of the unit of work's copy, checking pages against projects and scans.

        :param tables: Every table of the working copy.
        :type tables: InMemoryTables
        :param snapshot: The committed tables as they were when this unit of work began, which tell the revision a
                         page was read at.
        :type snapshot: InMemoryTables
        :param committed: The committed tables shared with every unit of work, whose page revisions another
                          transaction may have raised since this one began.
        :type committed: InMemoryTables
        """
        super().__init__(tables.pages, tables)
        self._snapshot = snapshot
        self._committed = committed

    @override
    async def update(self, entity: Page) -> Page:
        """Replace the stored state of a page, raising its revision, as the version counter of the table does.

        A database refuses a write over a row that another transaction changed after it was read, and over a page
        read before an earlier write of this transaction. A page this transaction wrote already carries the revision
        of the working copy, which a committed row has not reached, so the committed row is compared with the
        snapshot taken when the transaction began and not with the page.

        :param entity: Page with its new state and the revision it was read at.
        :type entity: Page
        :returns: The page as stored, with its revision raised by one.
        :rtype: Page
        :raises NotFoundError: If the page, or a project or scan it refers to, is not stored.
        :raises ConflictError: If its new state takes a unique value of another page.
        :raises ConcurrentChangeError: If a transaction that committed after this one began changed the page, or the
                                       page was read before an earlier write of this transaction.
        """
        working = self._rows.get(entity.id)
        committed, seen = self._committed.pages.get(entity.id), self._snapshot.pages.get(entity.id)
        if (working is not None and working.revision != entity.revision) or (
            committed is not None and seen is not None and committed.revision != seen.revision
        ):
            raise ConcurrentChangeError
        return await super().update(evolve(entity, revision=entity.revision + 1))

    @override
    def _check(self, entity: Page) -> None:
        """Require the page's project and scan, an order key new to the project, and a part of a scan of no page.

        A page without a scan takes no part of one, as a null in a unique key of the database matches nothing.

        :param entity: Page about to be stored.
        :type entity: Page
        :raises NotFoundError: If the project, or the scan the page names, is not stored.
        :raises ConflictError: If another page of the project has the order key, or another page shows the same slot
                               of the same scan.
        """
        require(self._tables.projects, entity.project_id)
        require(self._tables.scans, entity.scan_id)
        self._require_unique(entity, attrgetter('project_id', 'order_key'))
        if entity.scan_id is not None:
            self._require_unique(entity, attrgetter('scan_id', 'slot'))

    @override
    def _cascade(self, entity: Page) -> None:
        """Remove the page's versions, records, states, changes and sections, and leave a project it covered coverless.

        :param entity: Page just removed.
        :type entity: Page
        """
        remove_where(self._tables.pagination_sections, lambda section: section.first_page_id == entity.id)
        remove_where(self._tables.page_versions, lambda version: version.page_id == entity.id)
        remove_where(self._tables.page_stages, lambda stage: stage.page_id == entity.id)
        remove_where(self._tables.page_step_states, lambda state: state.page_id == entity.id)
        remove_where(self._tables.page_step_changes, lambda change: change.page_id == entity.id)
        if (project := self._tables.projects.get(entity.project_id)) is not None and project.cover_page_id == entity.id:
            self._tables.projects[project.id] = evolve(project, cover_page_id=None)

    @override
    async def list_for_project(
        self, project_id: ProjectId, request: SliceRequest, *, included_only: bool = False
    ) -> Slice[Page]:
        """Return one window of a project's pages in the byte order of their order keys.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :param included_only: Whether to leave out the pages kept out of the book.
        :type included_only: bool
        :returns: The pages of the window and the number of the pages listed, before the window.
        :rtype: Slice[Page]
        """
        pages = self._in_book_order(
            page
            for page in self._rows.values()
            if page.project_id == project_id and (page.included or not included_only)
        )
        return Slice(items=pages[request.offset : request.offset + request.limit], total=len(pages))

    @override
    async def list_by_ids(self, project_id: ProjectId, page_ids: Collection[PageId]) -> Sequence[Page]:
        """Return the given pages of a project in the byte order of their order keys.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param page_ids: Identifiers of the pages to read.
        :type page_ids: Collection[PageId]
        :returns: The pages in book order.
        :rtype: Sequence[Page]
        :raises NotFoundError: If an identifier names no page of the project.
        """
        wanted = set(page_ids)
        pages = [page for page in self._rows.values() if page.project_id == project_id and page.id in wanted]
        if missing := wanted - {page.id for page in pages}:
            raise NotFoundError(*missing)
        return self._in_book_order(pages)

    @override
    async def list_range(self, project_id: ProjectId, first_key: str, last_key: str) -> Sequence[Page]:
        """Return the pages whose order keys lie between the two keys in byte order, both included.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param first_key: Smallest order key of the range.
        :type first_key: str
        :param last_key: Greatest order key of the range.
        :type last_key: str
        :returns: The pages of the range in book order.
        :rtype: Sequence[Page]
        """
        lower, upper = first_key.encode(), last_key.encode()
        return self._in_book_order(
            page
            for page in self._rows.values()
            if page.project_id == project_id and lower <= page.order_key.encode() <= upper
        )

    @override
    async def list_for_source(self, project_id: ProjectId, source_id: SourceId) -> Sequence[Page]:
        """Return the pages whose scans belong to the source, in book order.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param source_id: Source whose scans the pages show.
        :type source_id: SourceId
        :returns: The pages cut from the scans of the source.
        :rtype: Sequence[Page]
        """
        scan_ids = {scan.id for scan in self._tables.scans.values() if scan.source_id == source_id}
        return self._in_book_order(
            page for page in self._rows.values() if page.project_id == project_id and page.scan_id in scan_ids
        )

    @override
    async def neighbour_key(
        self, project_id: ProjectId, key: str, side: Side, *, excluding: Collection[PageId] = ()
    ) -> str | None:
        """Return the nearest order key on one side of ``key``, without the excluded pages.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param key: Order key the neighbour is looked up from.
        :type key: str
        :param side: Whether to look before or after ``key``.
        :type side: Side
        :param excluding: Pages that do not count as neighbours.
        :type excluding: Collection[PageId]
        :returns: The nearest key on that side, or None when no page lies there.
        :rtype: str | None
        """
        pivot = key.encode()
        keys = [
            page.order_key for page in self._rows.values() if page.project_id == project_id and page.id not in excluding
        ]
        if side is Side.BEFORE:
            return max((other for other in keys if other.encode() < pivot), key=str.encode, default=None)
        return min((other for other in keys if other.encode() > pivot), key=str.encode, default=None)

    @override
    async def update_many(self, pages: Sequence[Page]) -> None:
        """Replace the stored state of several pages, all of them or none, as one database transaction does.

        :param pages: Pages with their new state.
        :type pages: Sequence[Page]
        :raises NotFoundError: If a page is not stored.
        :raises ConflictError: If the new state of a page takes a key another page has.
        """
        before = dict(self._rows)
        try:
            for page in pages:
                await self.update(page)
        except DomainError:
            self._rows.clear()
            self._rows.update(before)
            raise

    @staticmethod
    def _in_book_order(pages: Iterable[Page]) -> list[Page]:
        """Sort pages by the byte order of their order keys.

        :param pages: Pages of one project.
        :type pages: Iterable[Page]
        :returns: The pages in book order.
        :rtype: list[Page]
        """
        return sorted(pages, key=lambda page: page.order_key.encode())

    @override
    async def list_for_scan(self, scan_id: ScanId) -> Sequence[Page]:
        """Return the pages cut from one scan by their slot.

        :param scan_id: Scan the pages were cut from.
        :type scan_id: ScanId
        :returns: Every page that names the scan.
        :rtype: Sequence[Page]
        """
        return sorted((page for page in self._rows.values() if page.scan_id == scan_id), key=attrgetter('slot'))

    @override
    async def count_before(self, page: Page) -> int:
        """Count the project's pages whose order key is smaller in byte order.

        :param page: Stored page of the project.
        :type page: Page
        :returns: The position of the page in the book, from zero.
        :rtype: int
        """
        before = page.order_key.encode()
        return sum(
            1
            for other in self._rows.values()
            if other.project_id == page.project_id and other.order_key.encode() < before
        )

    @override
    async def last_order_key(self, project_id: ProjectId) -> str | None:
        """Return the greatest order key of the project's pages in byte order.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :returns: The order key of the last page, or None for a book without pages.
        :rtype: str | None
        """
        keys = [page.order_key for page in self._rows.values() if page.project_id == project_id]
        return max(keys, key=str.encode, default=None)


class InMemoryPaginationSectionRepository(
    InMemoryRepository[PaginationSection, PaginationSectionId], PaginationSectionRepository
):
    """The pagination sections of the books."""

    def __init__(self, tables: InMemoryTables) -> None:
        """Work on the section table of the unit of work's copy, checking sections against projects and pages.

        :param tables: Every table of the working copy.
        :type tables: InMemoryTables
        """
        super().__init__(tables.pagination_sections, tables)

    @override
    def _check(self, entity: PaginationSection) -> None:
        """Require the project of the section and the page it starts at, as its foreign keys do.

        :param entity: Section about to be stored.
        :type entity: PaginationSection
        :raises NotFoundError: If the project or the first page is not stored.
        """
        require(self._tables.projects, entity.project_id)
        require(self._tables.pages, entity.first_page_id)

    @override
    async def list_for_project(self, project_id: ProjectId) -> Sequence[PaginationSection]:
        """Return the sections of a project in the order they were made, ties by identifier.

        :param project_id: Project owning the sections.
        :type project_id: ProjectId
        :returns: Every section of the project.
        :rtype: Sequence[PaginationSection]
        """
        return sorted(
            (section for section in self._rows.values() if section.project_id == project_id),
            key=lambda section: (section.created_at, section.id),
        )


class InMemoryPageVersionRepository(InMemoryRepository[PageVersion, PageVersionId], PageVersionRepository):
    """Versions of the pages of the book."""

    def __init__(self, tables: InMemoryTables, *, snapshot: InMemoryTables, committed: InMemoryTables) -> None:
        """Work on the page version table of the unit of work's copy, checking versions against pages.

        :param tables: Every table of the working copy.
        :type tables: InMemoryTables
        :param snapshot: The committed tables as the transaction began, which tell its own rows from others'.
        :type snapshot: InMemoryTables
        :param committed: The committed tables shared with every unit of work, which another transaction may have
                          changed since this one began.
        :type committed: InMemoryTables
        """
        super().__init__(tables.page_versions, tables)
        self._snapshot = snapshot
        self._committed = committed

    @override
    async def update(self, entity: PageVersion) -> PageVersion:
        """Replace the stored state of a version, which another transaction may have deleted with its page meanwhile.

        A database statement updates no row then, so this fails like it instead of letting the commit bring the
        deleted row back.

        :param entity: Version with its new state.
        :type entity: PageVersion
        :returns: The version as stored.
        :rtype: PageVersion
        :raises NotFoundError: If the version, or a version or page it refers to, is not stored, or was deleted by a
                               transaction that committed after this one began.
        :raises ConflictError: If its new state takes a unique value of another version.
        """
        if entity.id in self._snapshot.page_versions and entity.id not in self._committed.page_versions:
            raise NotFoundError(entity.id)
        return await super().update(entity)

    @override
    def _check(self, entity: PageVersion) -> None:
        """Require the version's page and input version.

        :param entity: Version about to be stored.
        :type entity: PageVersion
        :raises NotFoundError: If the page, or the input version the version names, is not stored.
        """
        require(self._tables.pages, entity.page_id)
        require(self._rows, entity.input_id)

    @override
    def _cascade(self, entity: PageVersion) -> None:
        """Leave the versions the removed one fed, and the stages it headed, without it, as ``SET NULL`` does.

        :param entity: Version just removed.
        :type entity: PageVersion
        """
        for version in [version for version in self._rows.values() if version.input_id == entity.id]:
            self._rows[version.id] = evolve(version, input_id=None)
        for stage in [stage for stage in self._tables.page_stages.values() if stage.head_version_id == entity.id]:
            self._tables.page_stages[stage.key] = evolve(stage, head_version_id=None)

    @override
    async def list_for_page(self, page_id: PageId) -> Sequence[PageVersion]:
        """Return the versions of one page, the earliest first, ties by identifier.

        :param page_id: Page owning the versions.
        :type page_id: PageId
        :returns: Every version of the page.
        :rtype: Sequence[PageVersion]
        """
        return sorted(
            (version for version in self._rows.values() if version.page_id == page_id),
            key=attrgetter('created_at', ID_ATTRIBUTE),
        )

    @override
    async def list_to_prepare(self, project_id: ProjectId, processor_keys: Collection[str]) -> Sequence[PageVersion]:
        """Return the pending and failed versions of the project's pages made by the given processors.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param processor_keys: Keys of the processors whose versions are returned.
        :type processor_keys: Collection[str]
        :returns: The versions still to prepare, the earliest first, ties by identifier.
        :rtype: Sequence[PageVersion]
        """
        waiting = {VersionState.PENDING, VersionState.FAILED}
        return sorted(
            (
                version
                for version in self._rows.values()
                if version.processor.key in processor_keys
                and version.state in waiting
                and self._tables.pages[version.page_id].project_id == project_id
            ),
            key=attrgetter('created_at', ID_ATTRIBUTE),
        )

    @override
    async def base_sizes(self, project_id: ProjectId) -> Sequence[PageSize]:
        """Return the recorded sizes of the base versions of the project's included pages cut from a scan.

        A leaf drawn in place of a scan is a version of the page order, so it is not counted.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :returns: The size of every such base version that records one.
        :rtype: Sequence[PageSize]
        """
        shown = {
            page.id
            for page in self._tables.pages.values()
            if page.project_id == project_id and page.included and page.origin is PageOrigin.SCAN
        }
        sizes = (
            PageSize.from_data(version.data)
            for version in self._rows.values()
            if version.page_id in shown and version.input_id is None and version.stage is Stage.PAGE_SPLIT
        )
        return [size for size in sizes if size is not None]

    @override
    async def list_base_versions(self, page_ids: Collection[PageId]) -> Sequence[PageVersion]:
        """Return the versions of the given pages that have no input version, the earliest first, ties by identifier.

        :param page_ids: Pages whose base versions are read.
        :type page_ids: Collection[PageId]
        :returns: The base versions of those pages.
        :rtype: Sequence[PageVersion]
        """
        return sorted(
            (version for version in self._rows.values() if version.page_id in page_ids and version.input_id is None),
            key=attrgetter('created_at', ID_ATTRIBUTE),
        )

    @override
    async def list_by_ids(self, version_ids: Collection[PageVersionId]) -> Sequence[PageVersion]:
        """Return the stored versions among the given identifiers, the earliest first, ties by identifier.

        :param version_ids: Identifiers of the versions to read.
        :type version_ids: Collection[PageVersionId]
        :returns: The versions found.
        :rtype: Sequence[PageVersion]
        """
        return sorted(
            (version for version in self._rows.values() if version.id in version_ids),
            key=attrgetter('created_at', ID_ATTRIBUTE),
        )

    @override
    async def find(self, version_id: PageVersionId) -> PageVersion | None:
        """Return the version with this identifier.

        :param version_id: Identifier of the version.
        :type version_id: PageVersionId
        :returns: The stored version, or None.
        :rtype: PageVersion | None
        """
        return self._rows.get(version_id)

    @override
    async def list_for_stage(
        self, page_id: PageId, stage: Stage | None, scale: VersionScale | None, request: SliceRequest
    ) -> Slice[PageVersion]:
        """Return a window of the versions of one page matching a stage and a scale, the earliest first.

        :param page_id: Page owning the versions.
        :type page_id: PageId
        :param stage: Stage listed, or None for every stage.
        :type stage: Stage | None
        :param scale: Scale listed, or None for both.
        :type scale: VersionScale | None
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :returns: The window and the number of versions that match.
        :rtype: Slice[PageVersion]
        """
        matching = sorted(
            (
                version
                for version in self._rows.values()
                if version.page_id == page_id
                and (stage is None or version.stage == stage)
                and (scale is None or version.scale == scale)
            ),
            key=attrgetter('created_at', ID_ATTRIBUTE),
        )
        return Slice(items=matching[request.offset : request.offset + request.limit], total=len(matching))

    @override
    async def collectable(
        self, project_id: ProjectId, older_than: datetime, previews_older_than: datetime
    ) -> Sequence[PageVersion]:
        """Return the old versions that are neither base versions nor in the chain of a current version.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param older_than: Full runs created before this moment may go.
        :type older_than: datetime
        :param previews_older_than: Previews created before this moment may go.
        :type previews_older_than: datetime
        :returns: The versions that may be deleted, the earliest first, ties by identifier.
        :rtype: Sequence[PageVersion]
        """
        pages = {page.id for page in self._tables.pages.values() if page.project_id == project_id}
        versions = {version.id: version for version in self._rows.values() if version.page_id in pages}
        heads = [
            stage.head_version_id
            for stage in self._tables.page_stages.values()
            if stage.page_id in pages and stage.head_version_id is not None
        ]
        eligible = [
            version.id
            for version in versions.values()
            if version.input_id is not None
            and version.files_removed_at is None
            and version.created_at < (previews_older_than if version.scale is VersionScale.PREVIEW else older_than)
        ]
        goes = collectable_versions(
            {version.id: version.input_id for version in versions.values()}, eligible=eligible, heads=heads
        )
        return sorted((versions[version_id] for version_id in goes), key=attrgetter('created_at', ID_ATTRIBUTE))

    @override
    async def delete_many(self, version_ids: Collection[PageVersionId]) -> None:
        """Remove the stored versions among the given ones, with the actions of the keys that refer to them.

        :param version_ids: Versions to remove.
        :type version_ids: Collection[PageVersionId]
        """
        for version_id in version_ids:
            if version_id in self._rows:
                await self.delete(version_id)


class InMemoryPageStageRepository(InMemoryRepository[PageStage, PageStageKey], PageStageRepository):
    """The current version of each stage of each page."""

    def __init__(self, tables: InMemoryTables) -> None:
        """Work on the page stage table of the unit of work's copy, checking records against pages, versions, recipes.

        :param tables: Every table of the working copy.
        :type tables: InMemoryTables
        """
        super().__init__(tables.page_stages, tables)
        self._identify = attrgetter(KEY_ATTRIBUTE)

    @override
    def _check(self, entity: PageStage) -> None:
        """Require the page, the head version and the recipe of the record.

        :param entity: Record about to be stored.
        :type entity: PageStage
        :raises NotFoundError: If the page, the head version or the recipe the record names is not stored.
        """
        require(self._tables.pages, entity.page_id)
        require(self._tables.page_versions, entity.head_version_id)
        require(self._tables.recipes, entity.recipe_id)

    @override
    async def save(self, stage: PageStage) -> PageStage:
        """Store the record, replacing the one of the same page and stage.

        :param stage: Record to store.
        :type stage: PageStage
        :returns: The record as stored.
        :rtype: PageStage
        :raises NotFoundError: If the page, the head version or the recipe is not stored.
        """
        self._check(stage)
        self._rows[stage.key] = stage
        return stage

    @override
    async def find(self, key: PageStageKey) -> PageStage | None:
        """Return the record of a stage of a page.

        :param key: Page and stage.
        :type key: PageStageKey
        :returns: The record, or None.
        :rtype: PageStage | None
        """
        return self._rows.get(key)

    @override
    async def list_for_page(self, page_id: PageId) -> Sequence[PageStage]:
        """Return the records of one page in the order of the stages.

        :param page_id: Page owning the records.
        :type page_id: PageId
        :returns: Every record of the page.
        :rtype: Sequence[PageStage]
        """
        order = list(Stage)
        return sorted(
            (stage for stage in self._rows.values() if stage.page_id == page_id), key=lambda s: order.index(s.stage)
        )

    @override
    async def list_for_pages(self, page_ids: Collection[PageId]) -> Sequence[PageStage]:
        """Return the records of several pages, by page and then in the order of the stages.

        :param page_ids: Pages whose records are read.
        :type page_ids: Collection[PageId]
        :returns: Every record of those pages.
        :rtype: Sequence[PageStage]
        """
        order = list(Stage)
        return sorted(
            (record for record in self._rows.values() if record.page_id in page_ids),
            key=lambda record: (str(record.page_id), order.index(record.stage)),
        )

    @override
    async def list_for_recipe(self, recipe_id: RecipeId) -> Sequence[PageStage]:
        """Return the records that name the recipe, by page identifier.

        :param recipe_id: Recipe whose pages are listed.
        :type recipe_id: RecipeId
        :returns: Every record that names the recipe.
        :rtype: Sequence[PageStage]
        """
        return sorted(
            (stage for stage in self._rows.values() if stage.recipe_id == recipe_id), key=attrgetter('page_id')
        )

    @override
    async def list_for_project_stage(self, project_id: ProjectId, stage: Stage) -> Sequence[PageStage]:
        """Return the records of one stage over the pages of a project, by page identifier.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param stage: The stage.
        :type stage: Stage
        :returns: Every record of the stage in the project.
        :rtype: Sequence[PageStage]
        """
        pages = {page.id for page in self._tables.pages.values() if page.project_id == project_id}
        return sorted(
            (record for record in self._rows.values() if record.stage == stage and record.page_id in pages),
            key=attrgetter('page_id'),
        )

    @override
    async def head_ids(self, project_id: ProjectId) -> Collection[PageVersionId]:
        """Return the distinct head versions of the stage records of the project's pages.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :returns: The identifiers of the current versions.
        :rtype: Collection[PageVersionId]
        """
        pages = {page.id for page in self._tables.pages.values() if page.project_id == project_id}
        return {
            record.head_version_id
            for record in self._rows.values()
            if record.page_id in pages and record.head_version_id is not None
        }

    @override
    async def variant_tally(self, project_id: ProjectId) -> Sequence[VariantTally]:
        """Count the pages each recipe processed, for every stage of the project, leaving out the pages with no image.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :returns: One tally for each recipe that processed a page.
        :rtype: Sequence[VariantTally]
        """
        counts: Counter[tuple[Stage, RecipeId]] = Counter(
            (record.stage, record.recipe_id)
            for record in self._rows.values()
            if record.recipe_id is not None
            and (page := self._tables.pages[record.page_id]).project_id == project_id
            and page.origin is not PageOrigin.PLACEHOLDER
        )
        return [
            VariantTally(stage=stage, recipe_id=recipe_id, pages=pages) for (stage, recipe_id), pages in counts.items()
        ]

    @override
    async def tally(self, project_ids: Collection[ProjectId]) -> Sequence[StageTally]:
        """Count the records of every stage of the given projects by state, leaving out the pages with no image.

        :param project_ids: Projects whose stages are counted.
        :type project_ids: Collection[ProjectId]
        :returns: One tally for each stage of each project that has a record.
        :rtype: Sequence[StageTally]
        """
        groups: dict[tuple[ProjectId, Stage], list[PageStage]] = {}
        for record in self._rows.values():
            page = self._tables.pages[record.page_id]
            if page.project_id in project_ids and page.origin is not PageOrigin.PLACEHOLDER:
                groups.setdefault((page.project_id, record.stage), []).append(record)

        def marked(record: PageStage) -> bool:
            """Tell whether a record that has not failed has a current version with a review mark.

            :param record: The record.
            :type record: PageStage
            :returns: Whether the record counts as marked for review.
            :rtype: bool
            """
            if record.state is StageState.FAILED or record.head_version_id is None:
                return False
            return self._tables.page_versions[record.head_version_id].review is not None

        return [
            StageTally(
                project_id=project_id,
                stage=stage,
                fresh=sum(record.state is StageState.FRESH for record in records),
                stale=sum(record.state is StageState.STALE for record in records),
                failed=sum(record.state is StageState.FAILED for record in records),
                review=sum(marked(record) for record in records),
                check=sum(record.state is not StageState.FRESH or marked(record) for record in records),
                partial=sum(
                    record.state is not StageState.FAILED and record.through_step is not None for record in records
                ),
            )
            for (project_id, stage), records in groups.items()
        ]

    @override
    async def step_tally(self, project_id: ProjectId) -> Sequence[StepTally]:
        """Count the pages a run stopped at each step, for every stage of the project, leaving out failed records.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :returns: One tally for each step of a stage that a page stopped at.
        :rtype: Sequence[StepTally]
        """
        counts: Counter[tuple[Stage, int]] = Counter(
            (record.stage, record.through_step)
            for record in self._rows.values()
            if record.through_step is not None
            and record.state is not StageState.FAILED
            and (page := self._tables.pages[record.page_id]).project_id == project_id
            and page.origin is not PageOrigin.PLACEHOLDER
        )
        return [
            StepTally(stage=stage, through_step=through_step, pages=pages)
            for (stage, through_step), pages in counts.items()
        ]


class InMemoryPageStepStateRepository(InMemoryRepository[PageStepState, PageStepKey], PageStepStateRepository):
    """Settings and manual edits of the steps of the pages."""

    def __init__(self, tables: InMemoryTables) -> None:
        """Work on the page step state table of the unit of work's copy, checking states against pages.

        :param tables: Every table of the working copy.
        :type tables: InMemoryTables
        """
        super().__init__(tables.page_step_states, tables)
        self._identify = attrgetter(KEY_ATTRIBUTE)

    @override
    def _check(self, entity: PageStepState) -> None:
        """Require the page of the state.

        :param entity: State about to be stored.
        :type entity: PageStepState
        :raises NotFoundError: If the page is not stored.
        """
        require(self._tables.pages, entity.page_id)

    @override
    async def save(self, state: PageStepState) -> PageStepState:
        """Store the state, replacing the one of the same page, stage and step.

        :param state: State to store.
        :type state: PageStepState
        :returns: The state as stored.
        :rtype: PageStepState
        :raises NotFoundError: If the page is not stored.
        """
        self._check(state)
        self._rows[state.key] = state
        return state

    @override
    async def find(self, key: PageStepKey) -> PageStepState | None:
        """Return the state of one step on one page.

        :param key: Page, stage and step.
        :type key: PageStepKey
        :returns: The state, or None.
        :rtype: PageStepState | None
        """
        return self._rows.get(key)

    @override
    async def list_for_page(self, page_id: PageId, stage: Stage | None = None) -> Sequence[PageStepState]:
        """Return the states of one page, by stage and step.

        :param page_id: Page owning the states.
        :type page_id: PageId
        :param stage: Stage listed, or None for every stage.
        :type stage: Stage | None
        :returns: The states of the page.
        :rtype: Sequence[PageStepState]
        """
        order = list(Stage)
        return sorted(
            (
                state
                for state in self._rows.values()
                if state.page_id == page_id and (stage is None or state.stage == stage)
            ),
            key=lambda state: (order.index(state.stage), str(state.step_id)),
        )

    @override
    async def list_for_step(
        self, page_ids: Collection[PageId], stage: Stage, step_id: StepId
    ) -> Sequence[PageStepState]:
        """Return the states one step of a stage has on several pages, by page identifier.

        :param page_ids: Pages whose states are read.
        :type page_ids: Collection[PageId]
        :param stage: Stage of the step.
        :type stage: Stage
        :param step_id: The step of a recipe.
        :type step_id: StepId
        :returns: The states of the step on those of the pages that have one.
        :rtype: Sequence[PageStepState]
        """
        return sorted(
            (
                state
                for state in self._rows.values()
                if state.page_id in page_ids and state.stage == stage and state.step_id == step_id
            ),
            key=lambda state: str(state.page_id),
        )


class InMemoryPageStepChangeRepository(InMemoryRepository[PageStepChange, PageStepChangeId], PageStepChangeRepository):
    """The history of the layers of the steps of the pages."""

    def __init__(self, tables: InMemoryTables) -> None:
        """Work on the page step change table of the unit of work's copy, checking changes against pages.

        :param tables: Every table of the working copy.
        :type tables: InMemoryTables
        """
        super().__init__(tables.page_step_changes, tables)

    @override
    async def add(self, entity: PageStepChange) -> PageStepChange:
        """Store a change, numbering it after the last change of its page.

        :param entity: Change to store.
        :type entity: PageStepChange
        :returns: The change as stored, with its sequence.
        :rtype: PageStepChange
        :raises ConflictError: If a change with this identifier is stored already.
        :raises NotFoundError: If the page is not stored.
        """
        last = max((change.sequence for change in self._rows.values() if change.page_id == entity.page_id), default=0)
        return await super().add(evolve(entity, sequence=last + 1))

    @override
    def _check(self, entity: PageStepChange) -> None:
        """Require the page of the change.

        :param entity: Change about to be stored.
        :type entity: PageStepChange
        :raises NotFoundError: If the page is not stored.
        """
        require(self._tables.pages, entity.page_id)

    @override
    async def list_for_page(self, page_id: PageId, stage: Stage | None = None) -> Sequence[PageStepChange]:
        """Return the changes of one page, by their sequence.

        :param page_id: Page the changes were made on.
        :type page_id: PageId
        :param stage: Stage listed, or None for every stage.
        :type stage: Stage | None
        :returns: The changes of the page.
        :rtype: Sequence[PageStepChange]
        """
        return sorted(
            (
                change
                for change in self._rows.values()
                if change.page_id == page_id and (stage is None or change.stage == stage)
            ),
            key=attrgetter('sequence'),
        )

    @override
    async def list_for_batch(self, batch_id: ChangeBatchId) -> Sequence[PageStepChange]:
        """Return the changes of one batch, by page and then by sequence.

        :param batch_id: Identifier the changes of the batch share.
        :type batch_id: ChangeBatchId
        :returns: The changes of the batch.
        :rtype: Sequence[PageStepChange]
        """
        return sorted(
            (change for change in self._rows.values() if change.batch_id == batch_id),
            key=attrgetter('page_id', 'sequence'),
        )

    @override
    async def list_undoing(self, change_ids: Collection[PageStepChangeId]) -> Sequence[PageStepChange]:
        """Return the changes that take back any of the given changes, by page and then by sequence.

        :param change_ids: Identifiers of the changes that may have been undone.
        :type change_ids: Collection[PageStepChangeId]
        :returns: The undos.
        :rtype: Sequence[PageStepChange]
        """
        wanted = set(change_ids)
        return sorted(
            (change for change in self._rows.values() if change.undoes in wanted),
            key=attrgetter('page_id', 'sequence'),
        )


class InMemoryBookPlaceRepository(InMemoryRepository[BookPlace, BookPlaceKey], BookPlaceRepository):
    """The places accounts left books at."""

    def __init__(self, tables: InMemoryTables) -> None:
        """Work on the book place table of the unit of work's copy, checking places against projects.

        :param tables: Every table of the working copy.
        :type tables: InMemoryTables
        """
        super().__init__(tables.book_places, tables)
        self._identify = attrgetter(KEY_ATTRIBUTE)

    @override
    def _check(self, entity: BookPlace) -> None:
        """Require the book of the place.

        The account is not checked, because accounts belong to fastapi-users and have no port.

        :param entity: Place about to be stored.
        :type entity: BookPlace
        :raises NotFoundError: If the book is not stored.
        """
        require(self._tables.projects, entity.project_id)

    @override
    async def save(self, place: BookPlace) -> BookPlace:
        """Store the place, replacing the one of the same account and book.

        :param place: Place to store.
        :type place: BookPlace
        :returns: The place as stored.
        :rtype: BookPlace
        :raises NotFoundError: If the book is not stored.
        """
        self._check(place)
        self._rows[place.key] = place
        return place

    @override
    async def find(self, key: BookPlaceKey) -> BookPlace | None:
        """Return the place of an account in a book.

        :param key: Account and book.
        :type key: BookPlaceKey
        :returns: The place, or None.
        :rtype: BookPlace | None
        """
        return self._rows.get(key)


class InMemoryRecipeRepository(InMemoryRepository[Recipe, RecipeId], RecipeRepository):
    """Recipes of the projects, one active per stage of a project."""

    def __init__(self, tables: InMemoryTables) -> None:
        """Work on the recipe table of the unit of work's copy, checking recipes against projects.

        :param tables: Every table of the working copy.
        :type tables: InMemoryTables
        """
        super().__init__(tables.recipes, tables)

    @override
    def _check(self, entity: Recipe) -> None:
        """Require the recipe's project, and no other active recipe of its stage, as the partial unique index does.

        :param entity: Recipe about to be stored.
        :type entity: Recipe
        :raises NotFoundError: If the project, or the profile the recipe refers to, is not stored.
        :raises ConflictError: If the recipe is active and another recipe of the stage is too.
        """
        require(self._tables.projects, entity.project_id)
        require(self._tables.recipe_profiles, entity.profile_id)
        if entity.active:
            self._require_unique(
                entity, lambda recipe: (recipe.project_id, recipe.stage) if recipe.active else recipe.id
            )

    @override
    def _cascade(self, entity: Recipe) -> None:
        """Leave the page stages the recipe processed without their recipe, as the database's ``SET NULL`` does.

        :param entity: Recipe just removed.
        :type entity: Recipe
        """
        for stage in [stage for stage in self._tables.page_stages.values() if stage.recipe_id == entity.id]:
            self._tables.page_stages[stage.key] = evolve(stage, recipe_id=None)
        remove_where(self._tables.recipe_rules, lambda rule: rule.recipe_id == entity.id)

    @override
    async def list_for_stage(self, project_id: ProjectId, stage: Stage) -> Sequence[Recipe]:
        """Return the recipes of one stage, the active one first, then by creation, ties by identifier.

        :param project_id: Project owning the recipes.
        :type project_id: ProjectId
        :param stage: The stage.
        :type stage: Stage
        :returns: The active recipe and the variants.
        :rtype: Sequence[Recipe]
        """
        return sorted(
            (recipe for recipe in self._rows.values() if recipe.project_id == project_id and recipe.stage == stage),
            key=lambda recipe: (not recipe.active, recipe.created_at, recipe.id),
        )

    @override
    async def find_active(self, project_id: ProjectId, stage: Stage) -> Recipe | None:
        """Return the active recipe of a stage of a project.

        :param project_id: Project owning the recipe.
        :type project_id: ProjectId
        :param stage: The stage.
        :type stage: Stage
        :returns: The active recipe, or None.
        :rtype: Recipe | None
        """
        return next(
            (
                recipe
                for recipe in self._rows.values()
                if recipe.project_id == project_id and recipe.stage == stage and recipe.active
            ),
            None,
        )

    @override
    async def list_active(self, project_id: ProjectId) -> Sequence[Recipe]:
        """Return the active recipe of every stage of a project that has one, in the order of the stages.

        :param project_id: Project owning the recipes.
        :type project_id: ProjectId
        :returns: The active recipes.
        :rtype: Sequence[Recipe]
        """
        return sorted(
            (recipe for recipe in self._rows.values() if recipe.project_id == project_id and recipe.active),
            key=lambda recipe: recipe.stage.position,
        )


class InMemoryRecipeRuleRepository(InMemoryRepository[RecipeRule, RecipeRuleId], RecipeRuleRepository):
    """The rules that send pages to recipes of a stage."""

    def __init__(self, tables: InMemoryTables) -> None:
        """Work on the rule table of the unit of work's copy, checking rules against projects and recipes.

        :param tables: Every table of the working copy.
        :type tables: InMemoryTables
        """
        super().__init__(tables.recipe_rules, tables)

    @override
    def _check(self, entity: RecipeRule) -> None:
        """Require the project and the recipe of the rule, and one rule for each condition of a stage, as its key does.

        :param entity: Rule about to be stored.
        :type entity: RecipeRule
        :raises NotFoundError: If the project or the recipe is not stored.
        :raises ConflictError: If the stage already has a rule for the condition and the group label.
        """
        require(self._tables.projects, entity.project_id)
        require(self._tables.recipes, entity.recipe_id)
        self._require_unique(entity, lambda rule: (rule.project_id, rule.stage, rule.condition, rule.group_label))

    @override
    async def list_for_stage(self, project_id: ProjectId, stage: Stage) -> Sequence[RecipeRule]:
        """Return the rules of one stage in the order they are tried, ties by identifier.

        :param project_id: Project owning the rules.
        :type project_id: ProjectId
        :param stage: The stage.
        :type stage: Stage
        :returns: The rules of the stage, the first to try first.
        :rtype: Sequence[RecipeRule]
        """
        return sorted(
            (rule for rule in self._rows.values() if rule.project_id == project_id and rule.stage is stage),
            key=lambda rule: (rule.order, rule.id),
        )


class InMemoryRecipeProfileRepository(InMemoryRepository[RecipeProfile, RecipeProfileId], RecipeProfileRepository):
    """The recipe profiles of the accounts, one default per stage of an account."""

    def __init__(self, tables: InMemoryTables) -> None:
        """Work on the profile table of the unit of work's copy.

        :param tables: Every table of the working copy.
        :type tables: InMemoryTables
        """
        super().__init__(tables.recipe_profiles, tables)

    @override
    def _cascade(self, entity: RecipeProfile) -> None:
        """Leave the recipes made from the profile without their profile, as the database's ``SET NULL`` does.

        :param entity: Profile just removed.
        :type entity: RecipeProfile
        """
        for recipe in [recipe for recipe in self._tables.recipes.values() if recipe.profile_id == entity.id]:
            self._tables.recipes[recipe.id] = evolve(recipe, profile_id=None)

    @override
    def _check(self, entity: RecipeProfile) -> None:
        """Require no other default profile of the stage of the account, as the partial unique index does.

        The account is not required to be stored, since accounts have no port.

        :param entity: Profile about to be stored.
        :type entity: RecipeProfile
        :raises ConflictError: If the profile is the default and another profile of the stage is too.
        """
        if entity.is_default:
            self._require_unique(
                entity, lambda profile: (profile.account_id, profile.stage) if profile.is_default else profile.id
            )

    @override
    async def list_for_account(self, account_id: AccountId, stage: Stage | None = None) -> Sequence[RecipeProfile]:
        """Return the account's profiles in the order of the stages, then by creation, ties by identifier.

        :param account_id: Account owning the profiles.
        :type account_id: AccountId
        :param stage: The stage whose profiles are wanted, or None for every stage.
        :type stage: Stage | None
        :returns: The profiles of the account.
        :rtype: Sequence[RecipeProfile]
        """
        return sorted(
            (
                profile
                for profile in self._rows.values()
                if profile.account_id == account_id and (stage is None or profile.stage is stage)
            ),
            key=lambda profile: (profile.stage.position, profile.created_at, profile.id),
        )

    @override
    async def find_default(self, account_id: AccountId, stage: Stage) -> RecipeProfile | None:
        """Return the profile a new book of the account starts a stage with.

        :param account_id: Account owning the profile.
        :type account_id: AccountId
        :param stage: The stage.
        :type stage: Stage
        :returns: The default profile of the stage, or None.
        :rtype: RecipeProfile | None
        """
        return next(
            (
                profile
                for profile in self._rows.values()
                if profile.account_id == account_id and profile.stage is stage and profile.is_default
            ),
            None,
        )


class InMemoryJobRepository(InMemoryRepository[Job, JobId], JobRepository):
    """Jobs of every project."""

    def __init__(
        self,
        tables: InMemoryTables,
        *,
        snapshot: InMemoryTables,
        committed: InMemoryTables,
        guards: dict[JobId, Job | None],
    ) -> None:
        """Work on the job table of the unit of work's copy, checking jobs against its projects.

        :param tables: Every table of the working copy.
        :type tables: InMemoryTables
        :param snapshot: The committed tables as the transaction began, which tell its own changes from others'.
        :type snapshot: InMemoryTables
        :param committed: The committed tables shared with every unit of work, read by a guarded write.
        :type committed: InMemoryTables
        :param guards: The committed row each guarded write judged, by job, which the unit of work checks on commit.
        :type guards: dict[JobId, Job | None]
        """
        super().__init__(tables.jobs, tables)
        self._snapshot = snapshot
        self._committed = committed
        self._guards = guards

    @override
    def _check(self, entity: Job) -> None:
        """Require the job's project, and no other active job of its group in it, as the partial unique indexes do.

        :param entity: Job about to be stored.
        :type entity: Job
        :raises NotFoundError: If the job's project is not stored.
        :raises ConflictError: If the job is a queued or running import, a job writing page images, or a job
                               processing versions, and the project has another active job of the same group.
        """
        require(self._tables.projects, entity.project_id)
        for position, (kinds, states) in enumerate(AT_MOST_ONE_JOB):
            if entity.kind in kinds and entity.state in states:
                # Every other job gets its own identifier as its value, so only a job of the same limit can match
                self._require_unique(entity, partial(self._limit_key, position=position, kinds=kinds, states=states))

    @staticmethod
    def _limit_key(job: Job, *, position: int, kinds: frozenset[JobKind], states: frozenset[JobState]) -> Hashable:
        """Key a job by the project when it is one the limit counts, and by its own identifier otherwise.

        :param job: The job.
        :type job: Job
        :param position: Position of the limit in ``AT_MOST_ONE_JOB``.
        :type position: int
        :param kinds: The kinds of job the limit counts.
        :type kinds: frozenset[JobKind]
        :param states: The states of job the limit counts.
        :type states: frozenset[JobState]
        :returns: The key, which equals another job's only when both are counted by the limit in one project.
        :rtype: Hashable
        """
        return (job.project_id, position) if job.kind in kinds and job.state in states else job.id

    @override
    def _cascade(self, entity: Job) -> None:
        """Leave the sources the job imported without their import job, as the database's ``SET NULL`` does.

        :param entity: Job just removed.
        :type entity: Job
        """
        for source in [source for source in self._tables.sources.values() if source.import_job_id == entity.id]:
            self._tables.sources[source.id] = evolve(source, import_job_id=None)

    @override
    async def list_for_project(self, project_id: ProjectId, states: Collection[JobState]) -> Sequence[Job]:
        """Return a project's jobs in the given states, newest first.

        :param project_id: Project owning the jobs.
        :type project_id: ProjectId
        :param states: States a returned job may be in.
        :type states: Collection[JobState]
        :returns: The matching jobs, most recently created first.
        :rtype: Sequence[Job]
        """
        jobs = (job for job in self._tables.jobs.values() if job.project_id == project_id and job.state in states)
        return sorted(jobs, key=attrgetter('created_at'), reverse=True)

    @override
    async def list_for_projects(
        self, project_ids: Collection[ProjectId], states: Collection[JobState]
    ) -> Sequence[Job]:
        """Return the jobs of several projects in the given states, newest first.

        :param project_ids: Projects owning the jobs.
        :type project_ids: Collection[ProjectId]
        :param states: States a returned job may be in.
        :type states: Collection[JobState]
        :returns: The matching jobs of all the projects, most recently created first.
        :rtype: Sequence[Job]
        """
        jobs = (job for job in self._tables.jobs.values() if job.project_id in project_ids and job.state in states)
        return sorted(jobs, key=attrgetter('created_at'), reverse=True)

    @override
    async def update_if_state(self, entity: Job, *, expected: Collection[JobState]) -> Job | None:
        """Replace the job in the working copy while its latest state is one of ``expected``.

        Like a database statement, the check sees the job as last committed by anyone, unless this transaction changed
        the job itself. Without locks, the committed row judged here is recorded, and the unit of work refuses to
        commit if another transaction has replaced it by then.

        :param entity: Job with its new state.
        :type entity: Job
        :param expected: States the stored job must be in for the replacement to happen.
        :type expected: Collection[JobState]
        :returns: The job as stored, or None when its latest state is not one of ``expected``.
        :rtype: Job | None
        :raises NotFoundError: If the job is not stored.
        """
        own = await self.get(entity.id)
        committed = self._committed.jobs.get(entity.id)
        changed_here = self._snapshot.jobs.get(entity.id) is not own
        if (current := own if changed_here else committed) is None:
            raise NotFoundError(entity.id)
        if current.state not in expected:
            return None
        self._guards.setdefault(entity.id, committed)
        self._rows[entity.id] = entity
        return entity


class InMemoryUnitOfWork(UnitOfWork):
    """A transaction over a private copy of the database that publishes only its own changes on commit.

    Rows are frozen entities, so a changed row is a different object: comparing identities against the snapshot taken
    when the transaction began finds exactly what this unit added, replaced or removed.

    :ivar projects: Project repository over the working copy.
    :ivar sources: Source repository over the working copy.
    :ivar scans: Scan repository over the working copy.
    :ivar pages: Page repository over the working copy.
    :ivar pagination_sections: Pagination section repository over the working copy.
    :ivar page_versions: Page version repository over the working copy.
    :ivar page_stages: Page stage repository over the working copy.
    :ivar page_step_states: Page step state repository over the working copy.
    :ivar page_step_changes: Page step change repository over the working copy.
    :ivar recipes: Recipe repository over the working copy.
    :ivar recipe_rules: Recipe rule repository over the working copy.
    :ivar recipe_profiles: Recipe profile repository over the working copy.
    :ivar jobs: Job repository over the working copy.
    :ivar book_places: Book place repository over the working copy.
    """

    def __init__(self, database: InMemoryDatabase) -> None:
        """Begin a transaction over ``database``.

        :param database: Committed state shared with every other unit of work.
        :type database: InMemoryDatabase
        """
        self._database = database
        self._begin()

    @staticmethod
    def _copy(tables: InMemoryTables) -> InMemoryTables:
        """Copy every table; the rows themselves are immutable and shared.

        :param tables: Tables to copy.
        :type tables: InMemoryTables
        :returns: New tables holding the same rows.
        :rtype: InMemoryTables
        """
        return InMemoryTables(**{table.name: dict(getattr(tables, table.name)) for table in fields(InMemoryTables)})

    def _begin(self) -> None:
        """Start a transaction from the committed state."""
        self._snapshot = self._copy(self._database.tables)
        self._tables = self._copy(self._database.tables)
        self._guards: dict[JobId, Job | None] = {}
        self.projects = InMemoryProjectRepository(self._tables)
        self.sources = InMemorySourceRepository(self._tables)
        self.scans = InMemoryScanRepository(self._tables)
        self.pages = InMemoryPageRepository(self._tables, snapshot=self._snapshot, committed=self._database.tables)
        self.pagination_sections = InMemoryPaginationSectionRepository(self._tables)
        self.page_versions = InMemoryPageVersionRepository(
            self._tables, snapshot=self._snapshot, committed=self._database.tables
        )
        self.page_stages = InMemoryPageStageRepository(self._tables)
        self.page_step_states = InMemoryPageStepStateRepository(self._tables)
        self.page_step_changes = InMemoryPageStepChangeRepository(self._tables)
        self.recipes = InMemoryRecipeRepository(self._tables)
        self.recipe_rules = InMemoryRecipeRuleRepository(self._tables)
        self.recipe_profiles = InMemoryRecipeProfileRepository(self._tables)
        self.jobs = InMemoryJobRepository(
            self._tables, snapshot=self._snapshot, committed=self._database.tables, guards=self._guards
        )
        self.book_places = InMemoryBookPlaceRepository(self._tables)

    @override
    async def commit(self) -> None:
        """Publish the rows this transaction added, replaced or removed, and begin a new transaction.

        A database would have kept a job written by a guarded write locked until now. Here another transaction may
        have committed that job meanwhile, and publishing this one would silently replace it, so the whole commit is
        refused instead. Nothing awaits between the check and the merge, so no other commit interleaves.

        :raises ConflictError: If a job this transaction wrote by ``update_if_state`` was committed by another
                               transaction since; the transaction is discarded then.
        """
        committed = self._database.tables
        if changed := [job_id for job_id, seen in self._guards.items() if committed.jobs.get(job_id) is not seen]:
            self._begin()
            raise ConflictError(*changed)
        for table in fields(InMemoryTables):
            before, after = getattr(self._snapshot, table.name), getattr(self._tables, table.name)
            target = getattr(committed, table.name)
            for key in before.keys() - after.keys():
                target.pop(key, None)
            target.update({key: row for key, row in after.items() if before.get(key) is not row})
        self._begin()

    @override
    async def rollback(self) -> None:
        """Discard every change of this transaction and begin a new one from the committed state."""
        self._begin()
