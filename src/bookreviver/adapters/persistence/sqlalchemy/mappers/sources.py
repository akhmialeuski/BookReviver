"""Mappers of the sources of a book and of the scans each source holds."""

from typing import override

from attrs import asdict

from bookreviver.adapters.persistence.sqlalchemy.mappers.base import RowMapper
from bookreviver.adapters.persistence.sqlalchemy.mappers.book import (
    contributors_from_json,
    contributors_to_json,
    identifiers_from_json,
    identifiers_to_json,
)
from bookreviver.adapters.persistence.sqlalchemy.tables import (
    ScanRow,
    SourceRow,
)
from bookreviver.domain.books import MetadataSuggestion
from bookreviver.domain.ids import (
    ScanId,
    SourceId,
)
from bookreviver.domain.images import Renditions
from bookreviver.domain.sources import Scan, ScanFacts, Source, SourceFile


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
            kind=row.kind,
            file_type=row.file_type,
            file_name=row.file_name,
            files=[SourceFile(**stored) for stored in row.files],
            size_bytes=row.size_bytes,
            sha256=row.sha256,
            scan_count=row.scan_count,
            metadata=row.metadata_,
            suggestion=MetadataSuggestion(
                title=row.suggestion['title'],
                contributors=contributors_from_json(row.suggestion['contributors']),
                publisher=row.suggestion['publisher'],
                publication_year=row.suggestion['publication_year'],
                languages=tuple(row.suggestion['languages']),
                identifiers=identifiers_from_json(row.suggestion['identifiers']),
                subjects=tuple(row.suggestion['subjects']),
            ),
            status=row.status,
            rejection=row.rejection,
            detail=row.detail,
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
            kind=entity.kind,
            file_type=entity.file_type,
            file_name=entity.file_name,
            files=[asdict(stored) for stored in entity.files],
            size_bytes=entity.size_bytes,
            sha256=entity.sha256,
            metadata_=dict(entity.metadata),
            suggestion={
                'title': entity.suggestion.title,
                'contributors': contributors_to_json(entity.suggestion.contributors),
                'publisher': entity.suggestion.publisher,
                'publication_year': entity.suggestion.publication_year,
                'languages': list(entity.suggestion.languages),
                'identifiers': identifiers_to_json(entity.suggestion.identifiers),
                'subjects': list(entity.suggestion.subjects),
            },
            status=entity.status,
            rejection=entity.rejection,
            detail=entity.detail,
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
            source_id=SourceId(row.source_id),
            number=row.number,
            source_label=row.source_label,
            facts=facts,
            renditions=Renditions(ready=row.renditions_ready, version=row.renditions_version, full=row.renditions_full),
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
            renditions_full=entity.renditions.full,
        )
