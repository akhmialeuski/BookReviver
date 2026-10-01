"""In-memory unit of work: repositories work on a copy of the database, and ``commit`` publishes only the changes.

The adapter backs the service and API tests and runs the same port contract suite as the SQLAlchemy adapter, so it
reproduces the behaviour services rely on rather than only storing rows. It mirrors the schema of the SQL tables and
transaction isolation:

- Foreign keys: a source, a scan, a page or a job needs its project, a scan its source, a version its page, and a
  source, a page or a version the import job, scan or input version it names. A project's cover is one of its own
  pages. The owner of a project is not checked, because accounts belong to fastapi-users and have no port.
- Unique keys: the digest of a source's main file within its project, the number of a scan within its source, the
  order key of a page within its project, the pair of a scan and a slot, and the project of a queued or running
  import job, so a project runs one import at a time.
- Referential actions: a project takes its sources, scans, pages and jobs with it, a source its scans, and a page
  its versions. A deleted cover page leaves its project without a cover, a deleted scan leaves its pages without
  their scan, a deleted job leaves the sources it imported
  without their import job, and a deleted version leaves the versions it fed without their input.
- Isolation: a unit of work reads and writes a private copy of the tables, and ``commit`` merges only the rows it
  added, replaced or removed, so two units of work touching different rows do not overwrite each other.

Each repository states its table's keys in two hooks of the generic repository, ``_check`` before a row is stored and
``_cascade`` after one is removed, so the generic operations stay in one place.
"""

from operator import attrgetter
from typing import TYPE_CHECKING, override

from attrs import define, evolve, field, fields

from bookreviver.domain.entities import Job, Page, PageVersion, Project, ProjectOverview, Scan, Source
from bookreviver.domain.enums import JobKind, JobState, Side
from bookreviver.domain.errors import ConflictError, DomainError, NotFoundError
from bookreviver.domain.ids import JobId, PageId, PageVersionId, ProjectId, ScanId, SourceId
from bookreviver.domain.values import Slice, SliceRequest
from bookreviver.ports.persistence import (
    JobRepository,
    PageRepository,
    PageVersionRepository,
    ProjectRepository,
    Repository,
    ScanRepository,
    SourceRepository,
    UnitOfWork,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Hashable, Iterable, Mapping, Sequence

    from bookreviver.domain.ids import AccountId

# Attribute holding the identifier of every entity addressed by one
ID_ATTRIBUTE: str = 'id'
# The kinds of job a project runs one of at a time, the rows of the partial unique index of the ``jobs`` table
ONE_ACTIVE_AT_A_TIME: frozenset[JobKind] = frozenset({JobKind.IMPORT_SOURCE})


@define(kw_only=True)
class InMemoryTables:
    """The rows of every table, keyed by identifier.

    :ivar projects: Projects by identifier.
    :ivar sources: Sources by identifier.
    :ivar scans: Scans by identifier.
    :ivar pages: Pages by identifier.
    :ivar page_versions: Page versions by identifier.
    :ivar jobs: Jobs by identifier.
    """

    projects: dict[ProjectId, Project] = field(factory=dict)
    sources: dict[SourceId, Source] = field(factory=dict)
    scans: dict[ScanId, Scan] = field(factory=dict)
    pages: dict[PageId, Page] = field(factory=dict)
    page_versions: dict[PageVersionId, PageVersion] = field(factory=dict)
    jobs: dict[JobId, Job] = field(factory=dict)

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
        """Remove the project's sources, scans, pages with their versions, and jobs, as the database cascade does.

        :param entity: Project just removed.
        :type entity: Project
        """
        doomed_pages = {page.id for page in self._tables.pages.values() if page.project_id == entity.id}
        remove_where(self._tables.page_versions, lambda version: version.page_id in doomed_pages)
        remove_where(self._tables.pages, lambda page: page.id in doomed_pages)
        remove_where(self._tables.sources, lambda source: source.project_id == entity.id)
        remove_where(self._tables.scans, lambda scan: scan.project_id == entity.id)
        remove_where(self._tables.jobs, lambda job: job.project_id == entity.id)

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

    def __init__(self, tables: InMemoryTables) -> None:
        """Work on the page table of the unit of work's copy, checking pages against projects and scans.

        :param tables: Every table of the working copy.
        :type tables: InMemoryTables
        """
        super().__init__(tables.pages, tables)

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
        """Remove the page's versions and leave a project it was the cover of without a cover, as the database does.

        :param entity: Page just removed.
        :type entity: Page
        """
        remove_where(self._tables.page_versions, lambda version: version.page_id == entity.id)
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


class InMemoryPageVersionRepository(InMemoryRepository[PageVersion, PageVersionId], PageVersionRepository):
    """Versions of the pages of the book."""

    def __init__(self, tables: InMemoryTables) -> None:
        """Work on the page version table of the unit of work's copy, checking versions against pages.

        :param tables: Every table of the working copy.
        :type tables: InMemoryTables
        """
        super().__init__(tables.page_versions, tables)

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
        """Leave the versions the removed one fed without their input, as the database's ``SET NULL`` does.

        :param entity: Version just removed.
        :type entity: PageVersion
        """
        for version in [version for version in self._rows.values() if version.input_id == entity.id]:
            self._rows[version.id] = evolve(version, input_id=None)

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
        """Require the job's project, and no other queued or running import in it, as the partial unique index does.

        :param entity: Job about to be stored.
        :type entity: Job
        :raises NotFoundError: If the job's project is not stored.
        :raises ConflictError: If the job is a queued or running import and the project has another.
        """
        require(self._tables.projects, entity.project_id)
        if entity.kind in ONE_ACTIVE_AT_A_TIME and entity.state in JobState.active():
            # Every other job gets its own identifier as its value, so only a queued or running import can match
            self._require_unique(
                entity,
                lambda job: (
                    job.project_id if job.kind in ONE_ACTIVE_AT_A_TIME and job.state in JobState.active() else job.id
                ),
            )

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
    :ivar page_versions: Page version repository over the working copy.
    :ivar jobs: Job repository over the working copy.
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
        self.pages = InMemoryPageRepository(self._tables)
        self.page_versions = InMemoryPageVersionRepository(self._tables)
        self.jobs = InMemoryJobRepository(
            self._tables, snapshot=self._snapshot, committed=self._database.tables, guards=self._guards
        )

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
