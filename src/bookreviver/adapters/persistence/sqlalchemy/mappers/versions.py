"""The mapper of the versions of the pages."""

from typing import TYPE_CHECKING, Any, override

from attrs import asdict

from bookreviver.adapters.persistence.sqlalchemy.mappers.base import RowMapper
from bookreviver.adapters.persistence.sqlalchemy.tables import PageVersionRow
from bookreviver.domain.geometry import Point, Quad, Transform, TransformKind
from bookreviver.domain.ids import (
    PageId,
    PageVersionId,
    StorageKey,
)
from bookreviver.domain.images import Renditions
from bookreviver.domain.versions import PageVersion, ProcessorRef

if TYPE_CHECKING:
    from collections.abc import Mapping


class PageVersionMapper(RowMapper[PageVersion, PageVersionRow]):
    """Translation of a page version, whose processor is flattened into columns and whose transform is JSON."""

    @override
    def to_entity(self, row: PageVersionRow) -> PageVersion:
        """Build the page version stored in ``row``.

        :param row: Page version row loaded from the database.
        :type row: PageVersionRow
        :returns: Page version with its processor, transform, data and renditions state.
        :rtype: PageVersion
        """
        return PageVersion(
            id=PageVersionId(row.id),
            page_id=PageId(row.page_id),
            stage=row.stage,
            processor=ProcessorRef(key=row.processor_key, version=row.processor_version),
            input_id=None if row.input_id is None else PageVersionId(row.input_id),
            params=row.params,
            transform=self._transform(row.transform),
            data=row.data,
            review=row.review,
            renditions=(
                None
                if row.renditions_ready is None or row.renditions_full is None
                else Renditions(ready=row.renditions_ready, full=row.renditions_full)
            ),
            state=row.state,
            scale=row.scale,
            edit_hash=row.edit_hash,
            tiles_ready=row.tiles_ready,
            created_at=row.created_at,
            mark=row.mark,
            comment=row.comment,
        )

    @override
    def to_row(self, entity: PageVersion) -> PageVersionRow:
        """Build the row of ``entity``; the renditions keep their readiness and full format, never a later version.

        :param entity: Page version to store.
        :type entity: PageVersion
        :returns: Transient page version row.
        :rtype: PageVersionRow
        """
        return PageVersionRow(
            id=entity.id,
            page_id=entity.page_id,
            stage=entity.stage,
            processor_key=entity.processor.key,
            processor_version=entity.processor.version,
            input_id=entity.input_id,
            params=dict(entity.params),
            transform=asdict(entity.transform),
            data=dict(entity.data),
            review=entity.review,
            renditions_ready=None if entity.renditions is None else entity.renditions.ready,
            renditions_full=None if entity.renditions is None else entity.renditions.full,
            state=entity.state,
            scale=entity.scale,
            edit_hash=entity.edit_hash,
            tiles_ready=entity.tiles_ready,
            created_at=entity.created_at,
            mark=entity.mark,
            comment=entity.comment,
        )

    @staticmethod
    def _transform(stored: Mapping[str, Any]) -> Transform:
        """Rebuild a transform from the JSON ``attrs.asdict`` made of it.

        :param stored: The transform as JSON, its quadrilateral a mapping of corners to points.
        :type stored: Mapping[str, Any]
        :returns: The transform with its kind and arguments.
        :rtype: Transform
        """
        quad = stored['quad']
        return Transform(
            kind=TransformKind(stored['kind']),
            quad=None if quad is None else Quad(**{corner: Point(**point) for corner, point in quad.items()}),
            angle=stored['angle'],
            mesh_key=None if stored['mesh_key'] is None else StorageKey(stored['mesh_key']),
            matrix=None if stored.get('matrix') is None else tuple(stored['matrix']),
        )
