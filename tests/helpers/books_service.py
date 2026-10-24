"""A books service for the tests of the services that open the book of their job through it."""

from typing import TYPE_CHECKING

from bookreviver.services.books import BooksService

if TYPE_CHECKING:
    from bookreviver.ports.persistence import BookDatabases, CatalogueDatabase
    from bookreviver.ports.storage import BookDirectories


def make_books_service(
    catalogue: CatalogueDatabase, *, databases: BookDatabases, files: BookDirectories
) -> BooksService:
    """Build the books service a job opens its book through, over the given book databases and book directories.

    :param catalogue: Catalogue database whose catalogue holds the library the books are opened from.
    :type catalogue: CatalogueDatabase
    :param databases: Opens the databases of the books.
    :type databases: BookDatabases
    :param files: The directories of the books.
    :type files: BookDirectories
    :returns: The books service.
    :rtype: BooksService
    """
    return BooksService(catalogue=catalogue, databases=databases, directories=files)
