"""The repository of the record of a book, the one row of the ``book_records`` table of the file of the book.

The record names no book, so its one row is keyed by a constant, and the identifier of the book an overview carries is
the one the library gives it.
"""

from typing import TYPE_CHECKING, override

from sqlalchemy import func, select

from bookreviver.adapters.persistence.sqlalchemy.mappers import BookRecordMapper
from bookreviver.adapters.persistence.sqlalchemy.rows import BookRecordRows
from bookreviver.adapters.persistence.sqlalchemy.tables import BOOK_RECORD_ID, PageRow, ScanRow, SourceRow
from bookreviver.domain.ids import PageId
from bookreviver.domain.library import BookOverview
from bookreviver.domain.pages import PageKind, PageOrigin
from bookreviver.domain.sources import SourceStatus
from bookreviver.ports.persistence import BookRecordRepository

if TYPE_CHECKING:
    from sqlalchemy import ScalarSelect
    from sqlalchemy.orm import Session

    from bookreviver.domain.books import BookRecord


class SqlAlchemyBookRecordRepository(BookRecordRepository):
    """The record of the book of the file, the one row of ``book_records``, with the counts and the cover of the book.

    The counts are scalar subqueries, so one statement returns every count.

    :ivar _rows: The rows of the table.
    :ivar _mapper: Mapper between the record and its row.
    """

    def __init__(self, session: Session) -> None:
        """Create the repository over the ``book_records`` table.

        :param session: Session of the transaction.
        :type session: Session
        """
        self._rows = BookRecordRows(session=session)
        self._mapper = BookRecordMapper()

    @override
    def get(self) -> BookRecord:
        """Return the record of the book.

        :returns: The stored record.
        :rtype: BookRecord
        :raises NotFoundError: If the file holds no record yet.
        """
        return self._mapper.to_entity(self._rows.get(BOOK_RECORD_ID))

    @override
    def add(self, book_record: BookRecord) -> BookRecord:
        """Store the record of the book under the key of the one row.

        :param book_record: The record to store.
        :type book_record: BookRecord
        :returns: The record as stored.
        :rtype: BookRecord
        :raises ConflictError: If the file holds a record already.
        """
        return self._mapper.to_entity(self._rows.add(self._mapper.to_row(book_record)))

    @override
    def update(self, book_record: BookRecord) -> BookRecord:
        """Replace the one row of the record.

        :param book_record: The record with its new state.
        :type book_record: BookRecord
        :returns: The record as stored.
        :rtype: BookRecord
        :raises NotFoundError: If the file holds no record, or the new cover is not a page of the book.
        """
        return self._mapper.to_entity(self._rows.update(self._mapper.to_row(book_record)))

    @override
    def overview(self) -> BookOverview:
        """Read the record, count the included pages, the sources and the scans of the book, and find its cover.

        The cover is the page of the kind ``cover`` that comes first in the book, else the first page of the book, and a
        book without pages has none.

        :returns: The record of the book with its counts and its cover.
        :rtype: BookOverview
        :raises NotFoundError: If the file holds no record yet.
        """
        book_record = self.get()
        pages, sources, scans, with_image = self._rows.session.execute(select(*self._book_counts())).one()
        cover = select(PageRow.id).order_by(PageRow.kind != PageKind.COVER, PageRow.order_key).limit(1)
        cover_id = self._rows.session.scalar(cover)
        return BookOverview(
            book_record=book_record,
            page_count=pages,
            source_count=sources,
            scan_count=scans,
            image_page_count=with_image,
            cover_page_id=None if cover_id is None else PageId(cover_id),
        )

    @staticmethod
    def _book_counts() -> tuple[ScalarSelect[int], ScalarSelect[int], ScalarSelect[int], ScalarSelect[int]]:
        """Return the scalar subqueries counting the included pages, the imported sources and the scans of the book.

        :returns: The count of included pages, of sources, of scans and of pages with an image, in this order.
        :rtype: tuple[ScalarSelect[int], ScalarSelect[int], ScalarSelect[int], ScalarSelect[int]]
        """
        pages, sources, scans, with_image = (
            select(func.count()).select_from(table).where(*conditions).scalar_subquery()
            for table, conditions in (
                (PageRow, (PageRow.included.is_(True),)),
                (SourceRow, (SourceRow.status == SourceStatus.IMPORTED,)),
                (ScanRow, ()),
                (PageRow, (PageRow.origin != PageOrigin.PLACEHOLDER,)),
            )
        )
        return pages, sources, scans, with_image
