"""Persistence ports: repositories per aggregate and the catalogue database that runs a use case's work on them.

The data lives in two kinds of store. The catalogue is one for the application and holds what belongs to the
accounts: the library, which says which account owns which book, and the recipe profiles that are still to move into
the books. Each book has a store of its own that holds everything of the book, its recipe profiles included, and nothing
of any account, so a book moves between instances as it is. Nothing refers across two stores by a key the store checks,
and every list of books goes through the library.

Services never see a database. They hand a function over the repositories of one store, and the store runs that
function whole on one transaction: the ``CatalogueDatabase`` does so with ``read`` and ``change`` over the
``CatalogueRepositories``, and the ``BookDatabase``, which is opened by the directory name of a book, does so with
``read`` and ``change`` over the ``BookRepositories`` of that book alone, beside ``version`` and ``snapshot``. A read
only reads, and a change commits when the function returns. The repositories are synchronous, because the function
runs from its first statement to its commit without returning to the event loop, which is what keeps a write short:
the adapter of a database runs it in a thread of its own.

The book is the aggregate. Every change of a book runs in ``BookDatabase.change``, which waits for any other change of
that book and reads the record of the book first, so it sees only what the previous change committed, and never for a
change of another book. A repository write inside a read is a programming error, reported as ``NoChangeOpenError``. The
function cannot open another transaction, because it holds repositories and not the store, so changes never
nest, and a use case that needs both stores runs one transaction of each.
"""

from bookreviver.ports.persistence.base import KeyedRepository, Repository
from bookreviver.ports.persistence.book import BookPlaceRepository, BookRecordRepository
from bookreviver.ports.persistence.databases import (
    DEFAULT_CHANGE_WAIT_SECONDS,
    BookDatabase,
    BookRepositories,
    CatalogueDatabase,
    CatalogueRepositories,
    ChangeWaitSeconds,
    NoChangeOpenError,
)
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

__all__ = [
    'DEFAULT_CHANGE_WAIT_SECONDS',
    'BookDatabase',
    'BookPlaceRepository',
    'BookRecordRepository',
    'BookRepositories',
    'CatalogueDatabase',
    'CatalogueRepositories',
    'ChangeWaitSeconds',
    'KeyedRepository',
    'LibraryRepository',
    'NoChangeOpenError',
    'PageHistoryRepository',
    'PageRepository',
    'PageStageRepository',
    'PageStepStateRepository',
    'PageVersionRepository',
    'PaginationSectionRepository',
    'RecipeProfileRepository',
    'RecipeRepository',
    'Repository',
    'ScanRepository',
    'SourceRepository',
    'StepValuesRepository',
]
