"""The content of a book as one transaction of it sees it: its record, and its pages with their versions.

``Book.read`` and ``Book.change`` hand their work the repositories of one transaction. ``BookContent`` is the book over
those repositories, built inside that work: ``content.pages`` are the pages of the book, a page holds its versions and
the current version of each of its stages, and what a stage reads on a page is asked of the page. A use case and a
plugin walk the book through these objects instead of joining the repositories themselves, so the rules of the book,
such as which version a stage reads, stand in one place.

Everything here is synchronous and reads through the repositories it was built over, so it lives for one transaction
and opens none. A change is still written through ``content.repos``, by the use case that owns the transaction.
"""

from typing import TYPE_CHECKING

from attrs import Factory, field, frozen

from bookreviver.core.pages import BookPages

if TYPE_CHECKING:
    from bookreviver.domain.books import BookRecord
    from bookreviver.ports.persistence import BookRepositories


@frozen
class BookContent:
    """A book as one transaction of it sees it.

    :ivar repos: Repositories of the transaction, through which a use case writes what it changes.
    :ivar pages: The pages of the book.
    """

    repos: BookRepositories
    pages: BookPages = field(
        init=False, eq=False, repr=False, default=Factory(lambda self: BookPages(self.repos), takes_self=True)
    )

    @property
    def record(self) -> BookRecord:
        """The description of the book and the settings of the work on it, as the transaction reads them."""
        return self.repos.book_record.get()
