"""A books service for the tests of the services that open the book of their job through it."""

from typing import TYPE_CHECKING

from bookreviver.services.books import BooksService
from tests.helpers.builders import make_book_tools

if TYPE_CHECKING:
    from bookreviver.ports.persistence import BookDatabases, UnitOfWork
    from bookreviver.ports.storage import BookDirectories


def make_books_service(uow: UnitOfWork, *, databases: BookDatabases, files: BookDirectories) -> BooksService:
    """Build the books service a job opens its book through, over the given book databases and book directories.

    Opening a book reads the library and opens its database and its directory, so the tools of the service are ones no
    test looks at.

    :param uow: Unit of work whose catalogue holds the library the books are opened from.
    :type uow: UnitOfWork
    :param databases: Opens the databases of the books.
    :type databases: BookDatabases
    :param files: The directories of the books.
    :type files: BookDirectories
    :returns: The books service.
    :rtype: BooksService
    """
    return BooksService(
        uow=uow,
        databases=databases,
        directories=files,
        tools=make_book_tools(),
    )
