"""Tests for the move of the directories of the books from ``projects/`` to ``books/`` under the storage root."""

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.storage.move import BookDirectoriesMove, MoveRefusedError, MoveReport

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.anyio

OLD_ROOT: str = 'projects'
NEW_ROOT: str = 'books'
FIRST_BOOK: str = str(uuid4())
SECOND_BOOK: str = str(uuid4())
DATABASE: str = 'book.db'
SOURCE_FILE: str = 'sources/one/scan.png'
DATABASE_CONTENT: bytes = b'database'
SOURCE_CONTENT: bytes = b'scan'
OTHER_CONTENT: bytes = b'other'
STRAY_FILE: str = 'notes.txt'


def _make_book(root: Path, directory: str, name: str, *, content: bytes = DATABASE_CONTENT) -> None:
    """Make the directory of a book with a database file and a source file in it.

    :param root: The storage root.
    :type root: Path
    :param directory: The directory of the books under the root, where the book is made.
    :type directory: str
    :param name: Name of the directory of the book.
    :type name: str
    :param content: Content of the database file, which tells two books of one name apart.
    :type content: bytes
    """
    book = root / directory / name
    (book / SOURCE_FILE).parent.mkdir(parents=True)
    (book / DATABASE).write_bytes(content)
    (book / SOURCE_FILE).write_bytes(SOURCE_CONTENT)


class TestBookDirectoriesMove:
    """Tests for the one pass that renames the directories of the books."""

    async def test_two_books_move_with_their_files_and_a_second_run_changes_nothing(self, tmp_path: Path) -> None:
        """Verify both directories arrive in ``books/`` whole, ``projects/`` goes, and running again is a no-op.

        :param tmp_path: Storage root of the test.
        :type tmp_path: Path
        """
        _make_book(tmp_path, OLD_ROOT, FIRST_BOOK)
        _make_book(tmp_path, OLD_ROOT, SECOND_BOOK)
        move = BookDirectoriesMove(tmp_path)

        first = await move.run()
        moved = sorted(path.name for path in (tmp_path / NEW_ROOT).iterdir())
        second = await move.run()

        expect(first == MoveReport(found=True, moved=2))
        expect(moved == sorted([FIRST_BOOK, SECOND_BOOK]))
        for name in (FIRST_BOOK, SECOND_BOOK):
            expect((tmp_path / NEW_ROOT / name / DATABASE).read_bytes() == DATABASE_CONTENT, name)
            expect((tmp_path / NEW_ROOT / name / SOURCE_FILE).read_bytes() == SOURCE_CONTENT, name)
        expect(not (tmp_path / OLD_ROOT).exists())
        expect(second == MoveReport(found=False, moved=0))
        expect(sorted(path.name for path in (tmp_path / NEW_ROOT).iterdir()) == moved)
        assert_expectations()

    async def test_a_target_that_exists_stops_the_move_before_any_book_moves(self, tmp_path: Path) -> None:
        """Verify a book already in ``books/`` under the same name refuses the move and leaves both trees as they were.

        :param tmp_path: Storage root of the test.
        :type tmp_path: Path
        """
        _make_book(tmp_path, OLD_ROOT, FIRST_BOOK)
        _make_book(tmp_path, OLD_ROOT, SECOND_BOOK)
        _make_book(tmp_path, NEW_ROOT, SECOND_BOOK, content=OTHER_CONTENT)

        with pytest.raises(MoveRefusedError, match=SECOND_BOOK):
            await BookDirectoriesMove(tmp_path).run()

        expect(sorted(path.name for path in (tmp_path / OLD_ROOT).iterdir()) == sorted([FIRST_BOOK, SECOND_BOOK]))
        expect(sorted(path.name for path in (tmp_path / NEW_ROOT).iterdir()) == [SECOND_BOOK])
        expect((tmp_path / NEW_ROOT / SECOND_BOOK / DATABASE).read_bytes() == OTHER_CONTENT)
        assert_expectations()

    async def test_a_file_among_the_books_stops_the_move(self, tmp_path: Path) -> None:
        """Verify an entry of ``projects/`` that is not a directory refuses the move, which leaves ``projects/`` alone.

        :param tmp_path: Storage root of the test.
        :type tmp_path: Path
        """
        _make_book(tmp_path, OLD_ROOT, FIRST_BOOK)
        (tmp_path / OLD_ROOT / STRAY_FILE).write_text('stray')

        with pytest.raises(MoveRefusedError, match=STRAY_FILE):
            await BookDirectoriesMove(tmp_path).run()

        expect(sorted(path.name for path in (tmp_path / OLD_ROOT).iterdir()) == sorted([FIRST_BOOK, STRAY_FILE]))
        expect(not (tmp_path / NEW_ROOT).exists())
        assert_expectations()
