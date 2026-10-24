"""A books service for the tests of the services that open the book of their job through it."""

from pathlib import Path
from typing import TYPE_CHECKING

from bookreviver.adapters.storage import LocalAssetStore, LocalBookArchive, LocalSourceStore
from bookreviver.services.books import BooksService, BookStores
from tests.helpers.builders import make_book_tools

if TYPE_CHECKING:
    from bookreviver.ports.persistence import BookDatabases, UnitOfWork

# A storage root no test reads, since opening a book touches no file
UNUSED_ROOT: Path = Path('unused-storage-root')


def make_books_service(uow: UnitOfWork, *, databases: BookDatabases) -> BooksService:
    """Build the books service a job opens its book through, over the given book databases.

    Opening a book reads the library and opens its database, so the stores and the tools of the service are ones no test
    looks at.

    :param uow: Unit of work whose catalogue holds the library the books are opened from.
    :type uow: UnitOfWork
    :param databases: Opens the databases of the books.
    :type databases: BookDatabases
    :returns: The books service.
    :rtype: BooksService
    """
    return BooksService(
        uow=uow,
        databases=databases,
        tools=make_book_tools(),
        files=BookStores(
            sources=LocalSourceStore(root=UNUSED_ROOT),
            assets=LocalAssetStore(root=UNUSED_ROOT),
            archive=LocalBookArchive(root=UNUSED_ROOT),
        ),
    )
