"""Repositories of the sources of a book and of the scans each source holds."""

from typing import TYPE_CHECKING, override

from advanced_alchemy.filters import CollectionFilter
from sqlalchemy import delete, select

from bookreviver.adapters.persistence.sqlalchemy.mappers import (
    ScanMapper,
    SourceMapper,
)
from bookreviver.adapters.persistence.sqlalchemy.repositories.base import SqlAlchemyRepository
from bookreviver.adapters.persistence.sqlalchemy.rows import (
    ScanRows,
    SourceRows,
)
from bookreviver.adapters.persistence.sqlalchemy.tables import (
    ScanRow,
    SourceRow,
)
from bookreviver.domain.ids import (
    ScanId,
    SourceId,
)
from bookreviver.domain.slices import Slice
from bookreviver.domain.sources import Scan, Source, SourceStatus
from bookreviver.ports.persistence import (
    ScanRepository,
    SourceRepository,
)

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from sqlalchemy.orm import Session

    from bookreviver.domain.slices import SliceRequest


class SqlAlchemySourceRepository(SqlAlchemyRepository[Source, SourceId, SourceRow], SourceRepository):
    """Sources, listed in import order and found by the digest of their main file."""

    def __init__(self, session: Session) -> None:
        """Create the repository over the ``sources`` table.

        :param session: Session of the transaction.
        :type session: Session
        """
        super().__init__(rows=SourceRows(session=session), mapper=SourceMapper())

    @override
    def list_all(self) -> Sequence[Source]:
        """Return the book's imported sources, the earliest import first, ties by identifier.

        :returns: Every imported source of the book in import order.
        :rtype: Sequence[Source]
        """
        rows = self._rows.get_many(
            order_by=[SourceRow.imported_at.asc(), SourceRow.id.asc()],
            status=SourceStatus.IMPORTED,
        )
        return [self._mapper.to_entity(row) for row in rows]

    @override
    def list_unimported(self) -> Sequence[Source]:
        """Return the book's rejected and skipped files, the earliest first, ties by identifier.

        :returns: Every rejected or skipped source of the book in the order it was recorded.
        :rtype: Sequence[Source]
        """
        rows = self._rows.get_many(
            CollectionFilter(field_name=SourceRow.status, values=[SourceStatus.REJECTED, SourceStatus.SKIPPED]),
            order_by=[SourceRow.imported_at.asc(), SourceRow.id.asc()],
        )
        return [self._mapper.to_entity(row) for row in rows]

    @override
    def delete_unimported(self) -> None:
        """Delete the book's rejected and skipped sources with one statement."""
        self._rows.session.execute(delete(SourceRow).where(SourceRow.status != SourceStatus.IMPORTED))

    @override
    def find_by_sha256(self, sha256: str) -> Source | None:
        """Return the book's imported source with this digest, found through the partial unique index.

        :param sha256: SHA-256 digest of a main file.
        :type sha256: str
        :returns: The imported source with this digest, or None.
        :rtype: Source | None
        """
        row = self._rows.get_one_or_none(sha256=sha256, status=SourceStatus.IMPORTED)
        return None if row is None else self._mapper.to_entity(row)


class SqlAlchemyScanRepository(SqlAlchemyRepository[Scan, ScanId, ScanRow], ScanRepository):
    """Scans, listed per source by number and over the book in the import order of their sources."""

    def __init__(self, session: Session) -> None:
        """Create the repository over the ``scans`` table.

        :param session: Session of the transaction.
        :type session: Session
        """
        super().__init__(rows=ScanRows(session=session), mapper=ScanMapper())

    @override
    def list_for_source(self, source_id: SourceId) -> Sequence[Scan]:
        """Return the scans of one source by number.

        :param source_id: Source holding the scans.
        :type source_id: SourceId
        :returns: Every scan of the source.
        :rtype: Sequence[Scan]
        """
        rows = self._rows.get_many(order_by=ScanRow.number.asc(), source_id=source_id)
        return [self._mapper.to_entity(row) for row in rows]

    @override
    def list_window(self, request: SliceRequest) -> Slice[Scan]:
        """Return a slice of the book's scans, source by source in import order and by number within one.

        The order comes from the sources, so the listing joins them; the total is a separate count, which stays
        correct for a slice past the end.

        :param request: Offset and limit of the slice.
        :type request: SliceRequest
        :returns: Scans of the slice and the total number of the book's scans.
        :rtype: Slice[Scan]
        """
        statement = (
            select(ScanRow)
            .join(SourceRow, ScanRow.source_id == SourceRow.id)
            .order_by(SourceRow.imported_at, SourceRow.id, ScanRow.number)
            .offset(request.offset)
            .limit(request.limit)
        )
        rows = self._rows.session.scalars(statement).all()
        return Slice(items=[self._mapper.to_entity(row) for row in rows], total=self._rows.count())

    @override
    def list_by_ids(self, scan_ids: Collection[ScanId]) -> Sequence[Scan]:
        """Return the stored scans among the given identifiers in one ``IN`` query.

        :param scan_ids: Scans to read.
        :type scan_ids: Collection[ScanId]
        :returns: The scans that are stored.
        :rtype: Sequence[Scan]
        """
        rows = self._rows.get_many_in(ScanRow.id, set(scan_ids))
        return [self._mapper.to_entity(row) for row in rows]

    @override
    def list_unready(self) -> Sequence[Scan]:
        """Return the book's scans whose renditions are not ready, in the order ``list_window`` lists them.

        :returns: The scans without ready renditions.
        :rtype: Sequence[Scan]
        """
        statement = (
            select(ScanRow)
            .join(SourceRow, ScanRow.source_id == SourceRow.id)
            .where(ScanRow.renditions_ready.is_(False))
            .order_by(SourceRow.imported_at, SourceRow.id, ScanRow.number)
        )
        return [self._mapper.to_entity(row) for row in self._rows.session.scalars(statement).all()]
