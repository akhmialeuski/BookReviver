"""Mappers between table rows and frozen domain entities, one per aggregate."""

from abc import ABC, abstractmethod
from typing import override

from bookreviver.adapters.persistence.sqlalchemy.tables import JobRow, PageRow, ProjectRow
from bookreviver.domain.entities import Job, Page, Project
from bookreviver.domain.ids import AccountId, JobId, ProjectId
from bookreviver.domain.values import BookDetails, PageAssets, PageFacts, Progress, SourceSummary


class RowMapper[EntityT, RowT](ABC):
    """Turns a row into its domain entity and an entity into a new, unattached row."""

    @abstractmethod
    def to_entity(self, row: RowT) -> EntityT:
        """Build the domain entity stored in ``row``."""

    @abstractmethod
    def to_row(self, entity: EntityT) -> RowT:
        """Build a row holding every field of ``entity``."""


class ProjectMapper(RowMapper[Project, ProjectRow]):
    """Flattens the description and the optional source summary into project columns."""

    @override
    def to_entity(self, row: ProjectRow) -> Project:
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
    """Flattens the facts and the asset state of a page into page columns."""

    @override
    def to_entity(self, row: PageRow) -> Page:
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


class JobMapper(RowMapper[Job, JobRow]):
    """Flattens the progress of a job into job columns."""

    @override
    def to_entity(self, row: JobRow) -> Job:
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
