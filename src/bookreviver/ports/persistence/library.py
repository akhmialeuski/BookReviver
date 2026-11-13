"""The repository port of the library of the catalogue, which says which account owns which book."""

from abc import abstractmethod
from typing import TYPE_CHECKING

from bookreviver.domain.ids import BookId
from bookreviver.domain.library import LibraryEntry
from bookreviver.ports.persistence.base import Repository

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.ids import AccountId
    from bookreviver.domain.slices import Slice, SliceRequest


class LibraryRepository(Repository[LibraryEntry, BookId]):
    """The library of the catalogue: which account owns which book, one entry for each book.

    ``add`` raises ``NotFoundError`` when the owner is not an account, and an account is not deleted while an entry
    names it.
    """

    @abstractmethod
    def list_for_owner(self, owner_id: AccountId, request: SliceRequest) -> Slice[LibraryEntry]:
        """Return the owner's books, the latest added first, ties by identifier.

        :param owner_id: Account owning the books.
        :type owner_id: AccountId
        :param request: Offset and limit of the window to return.
        :type request: SliceRequest
        :returns: The entries of the window and the number of all the owner's books.
        :rtype: Slice[LibraryEntry]
        """

    @abstractmethod
    def list_all(self) -> Sequence[LibraryEntry]:
        """Return the entry of every book of every account, for the passes of the start over all books.

        :returns: Every entry, by identifier.
        :rtype: Sequence[LibraryEntry]
        """
