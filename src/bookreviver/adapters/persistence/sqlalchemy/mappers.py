"""Mappers between table rows and frozen domain entities, one per aggregate.

A domain entity and a table row have different shapes, and neither can stand in for the other. The entity is a frozen
``attrs`` class with nested value objects: a :class:`~bookreviver.domain.entities.Project` holds a
:class:`~bookreviver.domain.values.BookDetails` and an optional :class:`~bookreviver.domain.values.SourceSummary`. The
row is a flat, mutable class instrumented by SQLAlchemy, which assigns its attributes on load and tracks their changes.
The domain may not import SQLAlchemy, which import-linter enforces, so the translation lives here: ``to_row`` spreads
the value objects over columns and ``to_entity`` gathers them back.

This is the Data Mapper pattern with explicit code. Imperative mapping of the domain classes is ruled out because the
ORM cannot assign attributes of a frozen class, and ``composite()`` is ruled out because it needs positional
constructors and a ``__composite_values__`` method on domain classes, which are keyword-only and must stay free of
storage concerns.
"""

from abc import ABC, abstractmethod
from typing import override

from attrs import asdict

from bookreviver.adapters.persistence.sqlalchemy.tables import JobRow, PageRow, ProjectRow, ScanRow, SourceRow
from bookreviver.domain.entities import Job, Page, Project, Scan, Source
from bookreviver.domain.ids import AccountId, JobId, ProjectId, ScanId, SourceId
from bookreviver.domain.values import (
    BookDetails,
    MetadataSuggestion,
    PageAssets,
    PageFacts,
    Progress,
    Renditions,
    ScanFacts,
    SourceFile,
    SourceSummary,
)


class RowMapper[EntityT, RowT](ABC):
    """Translation between one kind of domain entity and the row of its table.

    The generic repository of the adapter holds a mapper of this type, so one repository class serves every pair of
    entity and row.
    """

    @abstractmethod
    def to_entity(self, row: RowT) -> EntityT:
        """Build the domain entity stored in ``row``.

        :param row: Row loaded from the database.
        :type row: RowT
        :returns: Frozen entity holding every value of the row.
        :rtype: EntityT
        """

    @abstractmethod
    def to_row(self, entity: EntityT) -> RowT:
        """Build a new, unattached row holding every field of ``entity``.

        :param entity: Domain entity to store.
        :type entity: EntityT
        :returns: Transient row, ready to be added or merged into a session.
        :rtype: RowT
        """


class ProjectMapper(RowMapper[Project, ProjectRow]):
    """Translation of a project, whose description and optional source summary are flattened into project columns."""

    @override
    def to_entity(self, row: ProjectRow) -> Project:
        """Build the project stored in ``row``.

        The source summary is rebuilt only when both its kind and its import time are set, which is the state after
        the first import. Before it the project has no source.

        :param row: Project row loaded from the database.
        :type row: ProjectRow
        :returns: Project with its description and, after the first import, its source summary.
        :rtype: Project
        """
        source = None
        if row.source_kind is not None and row.source_imported_at is not None:
            source = SourceSummary(
                kind=row.source_kind,
                name=row.source_name,
                size_bytes=row.source_size_bytes,
                metadata=row.source_metadata,
                imported_at=row.source_imported_at,
            )
        details = BookDetails(
            title=row.title,
            authors=row.authors,
            publisher=row.publisher,
            publication_place=row.publication_place,
            publication_year=row.publication_year,
            edition=row.edition,
            series=row.series,
            volume=row.volume,
            language=row.language,
            orthography=row.orthography,
            notes=row.notes,
        )
        return Project(
            id=ProjectId(row.id),
            owner_id=AccountId(row.owner_id),
            details=details,
            source=source,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @override
    def to_row(self, entity: Project) -> ProjectRow:
        """Build the row of ``entity``, writing null kind and import time and empty values when it has no source.

        :param entity: Project to store.
        :type entity: Project
        :returns: Transient project row.
        :rtype: ProjectRow
        """
        details, source = entity.details, entity.source
        return ProjectRow(
            id=entity.id,
            owner_id=entity.owner_id,
            title=details.title,
            authors=details.authors,
            publisher=details.publisher,
            publication_place=details.publication_place,
            publication_year=details.publication_year,
            edition=details.edition,
            series=details.series,
            volume=details.volume,
            language=details.language,
            orthography=details.orthography,
            notes=details.notes,
            source_kind=source.kind if source else None,
            source_name=source.name if source else '',
            source_size_bytes=source.size_bytes if source else 0,
            source_metadata=dict(source.metadata) if source else {},
            source_imported_at=source.imported_at if source else None,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )


class PageMapper(RowMapper[Page, PageRow]):
    """Translation of a page, whose facts and asset state are flattened into page columns."""

    @override
    def to_entity(self, row: PageRow) -> Page:
        """Build the page stored in ``row``.

        :param row: Page row loaded from the database.
        :type row: PageRow
        :returns: Page with its facts and asset state.
        :rtype: Page
        """
        facts = PageFacts(
            width_px=row.width_px,
            height_px=row.height_px,
            color_mode=row.color_mode,
            dpi_x=row.dpi_x,
            dpi_y=row.dpi_y,
            bits_per_component=row.bits_per_component,
            image_format=row.image_format,
            width_mm=row.width_mm,
            height_mm=row.height_mm,
            has_text_layer=row.has_text_layer,
            source_file=row.source_file,
            extra=row.extra,
        )
        return Page(
            project_id=ProjectId(row.project_id),
            index=row.index,
            facts=facts,
            assets=PageAssets(ready=row.assets_ready, version=row.assets_version),
        )

    @override
    def to_row(self, entity: Page) -> PageRow:
        """Build the row of ``entity``.

        :param entity: Page to store.
        :type entity: Page
        :returns: Transient page row.
        :rtype: PageRow
        """
        facts = entity.facts
        return PageRow(
            project_id=entity.project_id,
            index=entity.index,
            width_px=facts.width_px,
            height_px=facts.height_px,
            color_mode=facts.color_mode,
            dpi_x=facts.dpi_x,
            dpi_y=facts.dpi_y,
            bits_per_component=facts.bits_per_component,
            image_format=facts.image_format,
            width_mm=facts.width_mm,
            height_mm=facts.height_mm,
            has_text_layer=facts.has_text_layer,
            source_file=facts.source_file,
            extra=dict(facts.extra),
            assets_ready=entity.assets.ready,
            assets_version=entity.assets.version,
        )


class SourceMapper(RowMapper[Source, SourceRow]):
    """Translation of a source, whose files and suggested description are stored as JSON."""

    @override
    def to_entity(self, row: SourceRow) -> Source:
        """Build the source stored in ``row``.

        :param row: Source row loaded from the database.
        :type row: SourceRow
        :returns: Source with its files, metadata and suggested description.
        :rtype: Source
        """
        return Source(
            id=SourceId(row.id),
            project_id=ProjectId(row.project_id),
            kind=row.kind,
            file_type=row.file_type,
            file_name=row.file_name,
            files=[SourceFile(**stored) for stored in row.files],
            size_bytes=row.size_bytes,
            sha256=row.sha256,
            scan_count=row.scan_count,
            metadata=row.metadata_,
            suggestion=MetadataSuggestion(**row.suggestion),
            import_job_id=None if row.import_job_id is None else JobId(row.import_job_id),
            imported_at=row.imported_at,
        )

    @override
    def to_row(self, entity: Source) -> SourceRow:
        """Build the row of ``entity``.

        :param entity: Source to store.
        :type entity: Source
        :returns: Transient source row.
        :rtype: SourceRow
        """
        return SourceRow(
            id=entity.id,
            project_id=entity.project_id,
            kind=entity.kind,
            file_type=entity.file_type,
            file_name=entity.file_name,
            files=[asdict(stored) for stored in entity.files],
            size_bytes=entity.size_bytes,
            sha256=entity.sha256,
            scan_count=entity.scan_count,
            metadata_=dict(entity.metadata),
            suggestion=asdict(entity.suggestion),
            import_job_id=entity.import_job_id,
            imported_at=entity.imported_at,
        )


class ScanMapper(RowMapper[Scan, ScanRow]):
    """Translation of a scan, whose facts and renditions state are flattened into scan columns."""

    @override
    def to_entity(self, row: ScanRow) -> Scan:
        """Build the scan stored in ``row``.

        :param row: Scan row loaded from the database.
        :type row: ScanRow
        :returns: Scan with its facts and renditions state.
        :rtype: Scan
        """
        facts = ScanFacts(
            width_px=row.width_px,
            height_px=row.height_px,
            color_mode=row.color_mode,
            dpi_x=row.dpi_x,
            dpi_y=row.dpi_y,
            bits_per_component=row.bits_per_component,
            image_format=row.image_format,
            width_mm=row.width_mm,
            height_mm=row.height_mm,
            has_text_layer=row.has_text_layer,
            extra=row.extra,
        )
        return Scan(
            id=ScanId(row.id),
            project_id=ProjectId(row.project_id),
            source_id=SourceId(row.source_id),
            number=row.number,
            source_label=row.source_label,
            facts=facts,
            renditions=Renditions(ready=row.renditions_ready, version=row.renditions_version),
        )

    @override
    def to_row(self, entity: Scan) -> ScanRow:
        """Build the row of ``entity``.

        :param entity: Scan to store.
        :type entity: Scan
        :returns: Transient scan row.
        :rtype: ScanRow
        """
        facts = entity.facts
        return ScanRow(
            id=entity.id,
            project_id=entity.project_id,
            source_id=entity.source_id,
            number=entity.number,
            source_label=entity.source_label,
            width_px=facts.width_px,
            height_px=facts.height_px,
            color_mode=facts.color_mode,
            dpi_x=facts.dpi_x,
            dpi_y=facts.dpi_y,
            bits_per_component=facts.bits_per_component,
            image_format=facts.image_format,
            width_mm=facts.width_mm,
            height_mm=facts.height_mm,
            has_text_layer=facts.has_text_layer,
            extra=dict(facts.extra),
            renditions_ready=entity.renditions.ready,
            renditions_version=entity.renditions.version,
        )


class JobMapper(RowMapper[Job, JobRow]):
    """Translation of a job, whose progress is flattened into job columns."""

    @override
    def to_entity(self, row: JobRow) -> Job:
        """Build the job stored in ``row``.

        :param row: Job row loaded from the database.
        :type row: JobRow
        :returns: Job with its state, progress and timestamps.
        :rtype: Job
        """
        return Job(
            id=JobId(row.id),
            project_id=ProjectId(row.project_id),
            kind=row.kind,
            state=row.state,
            progress=Progress(done=row.progress_done, total=row.progress_total),
            error=row.error,
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )

    @override
    def to_row(self, entity: Job) -> JobRow:
        """Build the row of ``entity``.

        :param entity: Job to store.
        :type entity: Job
        :returns: Transient job row.
        :rtype: JobRow
        """
        return JobRow(
            id=entity.id,
            project_id=entity.project_id,
            kind=entity.kind,
            state=entity.state,
            progress_done=entity.progress.done,
            progress_total=entity.progress.total,
            error=entity.error,
            created_at=entity.created_at,
            started_at=entity.started_at,
            finished_at=entity.finished_at,
        )
