"""The one pass that moves the directories of the books from ``projects/`` to ``books/`` under the storage root.

The releases before the book was told apart from the project kept the directory of every book in ``projects/<name>/``
under the storage root, and ``KeySegment.BOOKS`` now names that root ``books``. :class:`BookDirectoriesMove` renames
each directory of ``projects/`` into ``books/`` on the same file system, so no file is copied, and removes the empty
``projects/``. It first looks at every entry and moves nothing when one would land on a name that ``books/`` holds
already or is not the directory of a book. A run that was broken off half way is run again and moves what is left, and a
root with no ``projects/`` is left as it is, so the second run of a finished move changes nothing.
"""

from typing import TYPE_CHECKING, Final

from anyio import to_thread
from attrs import frozen

from bookreviver.domain.keys import KeySegment

if TYPE_CHECKING:
    from pathlib import Path

# The directory of the books under the storage root in the releases before ``books/``
OLD_BOOKS_DIRECTORY: Final = 'projects'
NOT_A_BOOK: Final = '{old} holds {names}, which are not directories of books, so nothing was moved.'
TARGET_EXISTS: Final = '{new} holds {names} already, so moving the books would overwrite them and nothing was moved.'
LIST_SEPARATOR: Final = ', '


class MoveRefusedError(RuntimeError):
    """The move cannot start, because an entry of the old directory has no place to go or is not a book."""


@frozen(kw_only=True)
class MoveReport:
    """What a move did.

    :ivar found: Whether the old directory of the books existed.
    :ivar moved: How many directories of books were moved.
    """

    found: bool
    moved: int


class BookDirectoriesMove:
    """Moves the directories of the books from the old directory under the storage root to the current one."""

    def __init__(self, root: Path) -> None:
        """Move the directories of the books under ``root``.

        :param root: The storage root, which holds ``projects/`` before the move and ``books/`` after it.
        :type root: Path
        """
        self._root = root

    async def run(self) -> MoveReport:
        """Move every directory of a book and remove the old directory, or do nothing when there is none.

        :returns: Whether the old directory existed, and how many directories were moved.
        :rtype: MoveReport
        :raises MoveRefusedError: If an entry of the old directory is not a directory, or its name is taken in the new
                                  directory.
        """
        return await to_thread.run_sync(self._move)

    def _move(self) -> MoveReport:
        """Check every entry of the old directory, then rename them one by one and remove the old directory.

        :returns: Whether the old directory existed, and how many directories were moved.
        :rtype: MoveReport
        :raises MoveRefusedError: If an entry of the old directory is not a directory, or its name is taken in the new
                                  directory.
        """
        old = self._root / OLD_BOOKS_DIRECTORY
        new = self._root / KeySegment.BOOKS
        if not old.is_dir():
            return MoveReport(found=False, moved=0)
        books = sorted(old.iterdir())
        if strays := [book.name for book in books if not book.is_dir()]:
            raise MoveRefusedError(NOT_A_BOOK.format(old=old, names=LIST_SEPARATOR.join(strays)))
        if taken := [book.name for book in books if (new / book.name).exists(follow_symlinks=False)]:
            raise MoveRefusedError(TARGET_EXISTS.format(new=new, names=LIST_SEPARATOR.join(taken)))
        new.mkdir(exist_ok=True)
        for book in books:
            book.rename(new / book.name)
        old.rmdir()
        return MoveReport(found=True, moved=len(books))
