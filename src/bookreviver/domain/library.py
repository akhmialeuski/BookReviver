"""The library of an account: which books it owns, and how a book is listed."""

from typing import TYPE_CHECKING

from attrs import field, frozen, validators

if TYPE_CHECKING:
    from datetime import datetime

    from bookreviver.domain.books import BookRecord
    from bookreviver.domain.ids import AccountId, BookDirectoryName, BookId, PageId
    from bookreviver.domain.stage_summaries import BookProgress


@frozen(kw_only=True)
class LibraryEntry:
    """A book in the library of the account that owns it, the only account that may read or change the book.

    :ivar book_id: The book.
    :ivar owner_id: Account owning the book.
    :ivar directory: Name of the directory of the book in the storage, which holds its database and its files.
    :ivar added_at: When the book joined the library, which orders the owner's list of books, the newest first.
    """

    book_id: BookId
    owner_id: AccountId
    directory: BookDirectoryName
    added_at: datetime

    @property
    def id(self) -> BookId:
        """The identifier the entry is stored under, which is its book's: a book is in one library."""
        return self.book_id


@frozen(kw_only=True)
class BookOverview:
    """A book as shown in a list, with its size counted when read.

    :ivar book_record: The record of the listed book.
    :ivar page_count: Number of pages of the book, without the pages kept out of it.
    :ivar source_count: Number of sources the book was assembled from.
    :ivar scan_count: Number of scans in all the sources.
    :ivar image_page_count: Number of pages with an image, kept out of the book or not, which a run goes over.
    :ivar cover_page_id: Page the library shows as the cover: the first page of the kind ``cover``, else the first page
                         of the book, or None for a book without pages.
    :ivar progress: How far the book has come along the pipeline, which a repository cannot tell and a service adds, or
                    None before it has.
    """

    book_record: BookRecord
    page_count: int = field(default=0, validator=validators.ge(0))
    source_count: int = field(default=0, validator=validators.ge(0))
    scan_count: int = field(default=0, validator=validators.ge(0))
    image_page_count: int = field(default=0, validator=validators.ge(0))
    cover_page_id: PageId | None = None
    progress: BookProgress | None = None
