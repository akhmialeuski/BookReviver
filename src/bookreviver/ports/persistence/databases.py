"""The stores that run the work of a use case on one transaction: the catalogue and the database of a book."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, NewType

from bookreviver.ports.book_parts import BookPart
from bookreviver.ports.persistence.book import BookPlaceRepository, BookRecordRepository
from bookreviver.ports.persistence.library import LibraryRepository
from bookreviver.ports.persistence.pages import PageRepository, PaginationSectionRepository
from bookreviver.ports.persistence.recipes import RecipeProfileRepository, RecipeRepository
from bookreviver.ports.persistence.sources import ScanRepository, SourceRepository
from bookreviver.ports.persistence.stages import PageStageRepository
from bookreviver.ports.persistence.step_state import (
    PageHistoryRepository,
    PageStepStateRepository,
    StepValuesRepository,
)
from bookreviver.ports.persistence.versions import PageVersionRepository

if TYPE_CHECKING:
    from collections.abc import Callable

    from bookreviver.domain.books import BookRecord


# How long, in seconds, a change waits for another change of the same book before it gives up with ``BookBusyError``
DEFAULT_CHANGE_WAIT_SECONDS: float = 30.0
# The dependency key of the limit, in seconds, that one catalogue database waits for a change with before it gives up
# with ``BookBusyError``. The container chooses it per scope, a shorter one for a catalogue database that serves an HTTP
# request than for one of a job, and hands it to the adapter that builds the catalogue database
ChangeWaitSeconds = NewType('ChangeWaitSeconds', float)


class NoChangeOpenError(RuntimeError):
    """A repository wrote inside the work of ``CatalogueDatabase.read``.

    It is a programming error and not a ``DomainError``, so it reaches the caller as a failure and no request can
    cause it.
    """

    def __init__(self) -> None:
        """Report the write with its fixed sentence."""
        super().__init__('A repository write needs a change, and read only reads.')


class CatalogueRepositories(ABC):
    """Every repository of one transaction of the catalogue, which the work of ``read`` and ``change`` receives.

    :ivar library: Repository of the library, which account owns which book, of this transaction.
    """

    library: LibraryRepository


class BookRepositories(ABC):
    """Every repository of one transaction of a book, which the work of ``BookDatabase.read`` and ``change`` receives.

    :ivar book_record: Repository of the record of the book, of this transaction.
    :ivar sources: Source repository of this transaction.
    :ivar scans: Scan repository of this transaction.
    :ivar pages: Page repository of this transaction.
    :ivar pagination_sections: Repository of the pagination sections of the book, of this transaction.
    :ivar page_versions: Page version repository of this transaction.
    :ivar page_stages: Page stage repository of this transaction.
    :ivar page_step_states: Repository of the settings and manual edits of the steps of the pages, of this transaction.
    :ivar page_history: Repository of the history of the steps of the pages, of this transaction.
    :ivar step_values: Repository of the values of the steps for the odd pages, the even pages and the groups, of this
                       transaction.
    :ivar recipes: Recipe repository of this transaction.
    :ivar recipe_profiles: Repository of the recipe profiles of the book, of this transaction.
    :ivar book_places: Repository of the place the reader left the book at, of this transaction.
    """

    book_record: BookRecordRepository
    sources: SourceRepository
    scans: ScanRepository
    pages: PageRepository
    pagination_sections: PaginationSectionRepository
    page_versions: PageVersionRepository
    page_stages: PageStageRepository
    page_step_states: PageStepStateRepository
    page_history: PageHistoryRepository
    step_values: StepValuesRepository
    recipes: RecipeRepository
    recipe_profiles: RecipeProfileRepository
    book_places: BookPlaceRepository


class CatalogueDatabase(ABC):
    """Runs the work of a use case on the repositories of the catalogue, one transaction for each call.

    The work is a synchronous function over the repositories of the catalogue, and the catalogue database runs it whole:
    the transaction begins before its first statement and ends when it returns or raises, and nothing of it is visible
    to others before it commits. A change commits when the work returns and rolls back when it raises, and the exception
    goes on to the caller. Nothing holds a connection between two calls, so a caller may wait as long as it needs
    between them.

    A catalogue database keeps no state between calls, so coroutines may share one and call it at once. Changes of one
    process that wait for the same store enter in the order they asked for it, so one that came early is never
    overtaken by one that came later, and each waits at most the wait limit of the catalogue database before it gives up
    with ``BookBusyError``. Changes of different stores never wait for each other. The transactions of one book are the
    work of its ``BookDatabase``.
    """

    @abstractmethod
    async def read[T](self, work: Callable[[CatalogueRepositories], T]) -> T:
        """Run ``work`` on a transaction of the catalogue that only reads, and return what it returns.

        Every read of the work sees the same committed state of the catalogue. A write is refused with
        ``NoChangeOpenError``.

        :param work: Function over the repositories of the transaction.
        :type work: Callable[[CatalogueRepositories], T]
        :returns: What the work returns.
        :rtype: T
        :raises NoChangeOpenError: If the work writes.
        """

    @abstractmethod
    async def change[T](self, work: Callable[[CatalogueRepositories], T]) -> T:
        """Run ``work`` on a transaction of the catalogue that writes, commit it when the work returns, and return it.

        Reads of the work see everything committed before it began plus its own writes. When the work raises, the
        transaction rolls back and the exception goes on.

        :param work: Function over the repositories of the transaction.
        :type work: Callable[[CatalogueRepositories], T]
        :returns: What the work returns.
        :rtype: T
        :raises BookBusyError: If the write did not begin within the wait limit of the catalogue database.
        """


class BookDatabase(BookPart, ABC):
    """Runs the work of a use case on the repositories of one transaction of one book, one transaction for each call.

    The work is a synchronous function over the ``BookRepositories`` of the book, and the database runs it whole: the
    transaction begins before its first statement and ends when it returns or raises, and nothing of it is visible to
    others before it commits. A change commits when the work returns and rolls back when it raises, and the exception
    goes on to the caller. Nothing holds a connection between two calls, so a caller may wait as long as it needs
    between them.

    A book database keeps no state between calls, so coroutines may share one and call it at once. Changes of one
    process that wait for the same book enter in the order they asked for it, so one that came early is never overtaken
    by one that came later, and each waits at most the wait limit of the database before it gives up with
    ``BookBusyError``. Changes of different books never wait for each other.

    A new book has a database that ``create`` makes from the blank database of the schema and gives its first record,
    the database of an imported book is taken in by ``adopt``, and ``close`` ends the use of the database before its
    file is deleted. The file lies in the directory of the book, which the storage port makes and deletes.
    """

    @abstractmethod
    async def read[T](self, work: Callable[[BookRepositories], T]) -> T:
        """Run ``work`` on a transaction of the book that only reads, and return what it returns.

        Every read of the work sees the same committed state of the book. A write is refused with
        ``NoChangeOpenError``.

        :param work: Function over the repositories of the transaction.
        :type work: Callable[[BookRepositories], T]
        :returns: What the work returns.
        :rtype: T
        :raises NotFoundError: If the book is not stored.
        :raises NoChangeOpenError: If the work writes.
        """

    @abstractmethod
    async def change[T](self, work: Callable[[BookRepositories, BookRecord], T]) -> T:
        """Run ``work`` on a transaction of the book that writes, alone among its changes, with its record as it is.

        The change waits for any other change of the same book to end, then reads the record of the book, so it sees
        what the previous change committed. It never waits for a change of another book or of the catalogue.

        :param work: Function over the repositories of the transaction and the record of the book read at its start.
        :type work: Callable[[BookRepositories, BookRecord], T]
        :returns: What the work returns.
        :rtype: T
        :raises NotFoundError: If the book is not stored.
        :raises BookBusyError: If the write did not begin within the wait limit of the database.
        """

    @abstractmethod
    async def version(self) -> int:
        """Return the version of the book, which every committed change that wrote a part of the book raised by one.

        :returns: The version of the book.
        :rtype: int
        :raises NotFoundError: If the book is not stored.
        :raises BookTooNewError: If the store of the book is at a revision this code does not ship.
        """

    @abstractmethod
    async def snapshot(self) -> bytes:
        """Return a copy of the store of the book as one committed state of it, for the archive of the book.

        The copy is the store in the form it keeps on disk, which :meth:`BookDatabase.adopt` takes back in another
        instance.

        :returns: The copy of the store.
        :rtype: bytes
        :raises NotFoundError: If the book is not stored.
        :raises BookTooNewError: If the store of the book is at a revision this code does not ship.
        """

    @abstractmethod
    async def create(self, book_record: BookRecord) -> None:
        """Make the database of a new book from the blank database of the schema, and store the first record.

        The directory of the book exists already and holds no database. The new database is at the head revision of the
        schema, and nothing is announced, since nobody can watch a book that is not listed yet.

        :param book_record: The record of the book.
        :type book_record: BookRecord
        :raises ConflictError: If the book has a database already.
        """

    @abstractmethod
    async def adopt(self) -> None:
        """Take the database file an archive placed as the database of a new book, bringing it to this release first.

        :raises BookTooNewError: If the file is at a revision this code does not ship.
        :raises InvalidArchiveError: If the file is not a database of a book, or holds no record of a book.
        """

    @abstractmethod
    async def close(self) -> None:
        """Wait for the transactions of the book in progress to end, then close the database for good.

        Every later call of the database, or of another database of the same book, raises ``NotFoundError``, so the
        file can be deleted. A database already closed is left as it is, so a deletion broken off half way can run
        again.
        """
