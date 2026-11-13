"""The repository of the versions of the pages."""

from itertools import batched
from typing import TYPE_CHECKING, Any, override

from advanced_alchemy.filters import LimitOffset
from sqlalchemy import and_, delete, or_, select

from bookreviver.adapters.persistence.sqlalchemy.mappers import PageVersionMapper
from bookreviver.adapters.persistence.sqlalchemy.repositories.base import SqlAlchemyRepository
from bookreviver.adapters.persistence.sqlalchemy.rows import PageVersionRows
from bookreviver.adapters.persistence.sqlalchemy.tables import (
    EMPTY_TEXT,
    PageRow,
    PageStageRow,
    PageVersionRow,
)
from bookreviver.domain.ids import PageVersionId
from bookreviver.domain.pages import PageOrigin
from bookreviver.domain.slices import Slice
from bookreviver.domain.stages import Stage
from bookreviver.domain.version_chains import collectable_versions
from bookreviver.domain.versions import PageSize, PageVersion, ResultMark, VersionScale, VersionState
from bookreviver.ports.persistence import PageVersionRepository

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence
    from datetime import datetime

    from sqlalchemy import ColumnElement
    from sqlalchemy.orm import Session

    from bookreviver.domain.ids import PageId
    from bookreviver.domain.slices import SliceRequest


class SqlAlchemyPageVersionRepository(
    SqlAlchemyRepository[PageVersion, PageVersionId, PageVersionRow], PageVersionRepository
):
    """Versions of the pages of the book, listed per page in the order they were created."""

    def __init__(self, session: Session) -> None:
        """Create the repository over the ``page_versions`` table.

        :param session: Session of the transaction.
        :type session: Session
        """
        super().__init__(rows=PageVersionRows(session=session), mapper=PageVersionMapper())

    @override
    def list_for_page(self, page_id: PageId) -> Sequence[PageVersion]:
        """Return the versions of one page, the earliest first, ties by identifier.

        :param page_id: Page owning the versions.
        :type page_id: PageId
        :returns: Every version of the page.
        :rtype: Sequence[PageVersion]
        """
        rows = self._rows.get_many(order_by=[PageVersionRow.created_at.asc(), PageVersionRow.id.asc()], page_id=page_id)
        return [self._mapper.to_entity(row) for row in rows]

    @override
    def list_to_prepare(self, processor_keys: Collection[str]) -> Sequence[PageVersion]:
        """Return the pending and failed versions of the book's pages made by the given processors.

        :param processor_keys: Keys of the processors whose versions are returned.
        :type processor_keys: Collection[str]
        :returns: The versions still to prepare, the earliest first, ties by identifier.
        :rtype: Sequence[PageVersion]
        """
        statement = (
            select(PageVersionRow)
            .join(PageRow, PageVersionRow.page_id == PageRow.id)
            .where(
                PageVersionRow.processor_key.in_(processor_keys),
                PageVersionRow.state.in_([VersionState.PENDING, VersionState.FAILED]),
            )
            .order_by(PageVersionRow.created_at, PageVersionRow.id)
        )
        return [self._mapper.to_entity(row) for row in self._rows.session.scalars(statement).all()]

    @override
    def base_sizes(self) -> Sequence[PageSize]:
        """Return the sizes the base versions of the book's included scan pages record in their data.

        A leaf drawn in place of a scan is a version of the page order, so it is not counted. Only the data column is
        read, and the sizes are taken from it here, since the column is JSON.

        :returns: The size of every such base version that records one.
        :rtype: Sequence[PageSize]
        """
        statement = (
            select(PageVersionRow.data)
            .join(PageRow, PageVersionRow.page_id == PageRow.id)
            .where(
                PageRow.included.is_(True),
                PageRow.origin == PageOrigin.SCAN,
                PageVersionRow.input_id.is_(None),
                PageVersionRow.stage == Stage.PAGE_SPLIT,
            )
        )
        stored: Sequence[dict[str, Any]] = self._rows.session.scalars(statement).all()
        sizes = (PageSize.from_data(data) for data in stored)
        return [size for size in sizes if size is not None]

    @override
    def list_base_versions(self, page_ids: Collection[PageId]) -> Sequence[PageVersion]:
        """Return the versions of the given pages that have no input version, in one ``IN`` query.

        :param page_ids: Pages whose base versions are read.
        :type page_ids: Collection[PageId]
        :returns: The base versions of those pages, the earliest first, ties by identifier.
        :rtype: Sequence[PageVersion]
        """
        rows = self._rows.get_many_in(
            PageVersionRow.page_id,
            page_ids,
            PageVersionRow.input_id.is_(None),
            order=[PageVersionRow.created_at, PageVersionRow.id],
        )
        return [self._mapper.to_entity(row) for row in rows]

    @override
    def list_by_ids(self, version_ids: Collection[PageVersionId]) -> Sequence[PageVersion]:
        """Return the stored versions among the given identifiers, in one ``IN`` query.

        :param version_ids: Identifiers of the versions to read.
        :type version_ids: Collection[PageVersionId]
        :returns: The versions found, the earliest first, ties by identifier.
        :rtype: Sequence[PageVersion]
        """
        rows = self._rows.get_many_in(
            PageVersionRow.id, version_ids, order=[PageVersionRow.created_at, PageVersionRow.id]
        )
        return [self._mapper.to_entity(row) for row in rows]

    @override
    def find(self, version_id: PageVersionId) -> PageVersion | None:
        """Return the version with this identifier.

        :param version_id: Identifier of the version.
        :type version_id: PageVersionId
        :returns: The stored version, or None.
        :rtype: PageVersion | None
        """
        row = self._rows.get_one_or_none(id=version_id)
        return None if row is None else self._mapper.to_entity(row)

    @override
    def list_for_stage(
        self,
        page_id: PageId,
        stage: Stage | None,
        scale: VersionScale | None,
        request: SliceRequest,
        mark: ResultMark | None = None,
    ) -> Slice[PageVersion]:
        """Return a window of the versions of one page matching a stage, a scale and a mark, the earliest first.

        :param page_id: Page owning the versions.
        :type page_id: PageId
        :param stage: Stage listed, or None for every stage.
        :type stage: Stage | None
        :param scale: Scale listed, or None for both.
        :type scale: VersionScale | None
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :param mark: Mark listed, or None for every version, marked or not.
        :type mark: ResultMark | None
        :returns: The window and the number of versions that match.
        :rtype: Slice[PageVersion]
        """
        filters: list[ColumnElement[bool]] = [PageVersionRow.page_id == page_id]
        if stage is not None:
            filters.append(PageVersionRow.stage == stage)
        if scale is not None:
            filters.append(PageVersionRow.scale == scale)
        if mark is not None:
            filters.append(PageVersionRow.mark == mark)
        rows, total = self._rows.get_many_and_count(
            LimitOffset(limit=request.limit, offset=request.offset),
            *filters,
            order_by=[PageVersionRow.created_at.asc(), PageVersionRow.id.asc()],
        )
        return Slice(items=[self._mapper.to_entity(row) for row in rows], total=total)

    @override
    def collectable(self, previews_older_than: datetime) -> Sequence[PageVersion]:
        """Return the versions that are neither base versions nor in the chain of a current version.

        The chains are followed here, from the heads of the stage records through the input of each version, over the
        two columns of the book's versions, read in one query. A recursive query, ``WITH RECURSIVE``, would follow
        them in the database; whether it is faster is a question for a measurement on a large book.

        :param previews_older_than: Previews created before this moment may go.
        :type previews_older_than: datetime
        :returns: The versions that may be deleted, the earliest first, ties by identifier.
        :rtype: Sequence[PageVersion]
        """
        session = self._rows.session
        may_go = and_(
            PageVersionRow.input_id.is_not(None),
            or_(PageVersionRow.mark.is_(None), PageVersionRow.mark != ResultMark.GOOD),
            PageVersionRow.comment == EMPTY_TEXT,
            or_(PageVersionRow.scale != VersionScale.PREVIEW, PageVersionRow.created_at < previews_older_than),
        )
        # The two columns of every version of the book, and whether it may go, since the chains are followed here
        rows = session.execute(
            select(PageVersionRow.id, PageVersionRow.input_id, may_go).join(
                PageRow, PageVersionRow.page_id == PageRow.id
            )
        ).all()
        heads = session.scalars(
            select(PageStageRow.head_version_id)
            .join(PageRow, PageStageRow.page_id == PageRow.id)
            .where(PageStageRow.head_version_id.is_not(None))
        ).all()
        goes = collectable_versions(
            {
                PageVersionId(version_id): None if input_id is None else PageVersionId(input_id)
                for version_id, input_id, _ in rows
            },
            eligible=[PageVersionId(version_id) for version_id, _, can_go in rows if can_go],
            heads=[PageVersionId(head) for head in heads if head is not None],
        )
        found = self._rows.get_many_in(PageVersionRow.id, goes, order=[PageVersionRow.created_at, PageVersionRow.id])
        return [self._mapper.to_entity(row) for row in found]

    @override
    def delete_many(self, version_ids: Collection[PageVersionId]) -> None:
        """Remove the stored versions among the given ones, with the actions of the keys that refer to them.

        :param version_ids: Versions to remove.
        :type version_ids: Collection[PageVersionId]
        """
        for chunk in batched(version_ids, self._rows.IN_CHUNK_SIZE, strict=False):
            self._rows.session.execute(delete(PageVersionRow).where(PageVersionRow.id.in_(chunk)))
