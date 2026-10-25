"""Tests for the command line of the migrations: the move of the directories of the books."""

from typing import TYPE_CHECKING, NamedTuple

import pytest
from click.testing import CliRunner
from delayed_assert import assert_expectations, expect

from bookreviver.app.migrate import migrate

if TYPE_CHECKING:
    from pathlib import Path

MOVE_COMMAND: str = 'move-book-directories'
DATA_DIR_VARIABLE: str = 'BOOKREVIVER_DATA_DIR'
SECRET_VARIABLE: str = 'BOOKREVIVER_AUTH__SECRET'
SECRET: str = 'a-secret-long-enough-for-the-settings'
STORAGE: str = 'storage'
OLD_ROOT: str = 'projects'
NEW_ROOT: str = 'books'
BOOK: str = 'first-book'
OTHER_BOOK: str = 'second-book'


class MoveCase(NamedTuple):
    """A storage root before the command, and what the command answers.

    :ivar old_books: Directories of books under ``projects/``, or ``None`` when there is no ``projects/``.
    :ivar new_books: Directories of books already under ``books/``.
    :ivar exit_code: Exit code of the command.
    :ivar says: Text the output of the command holds.
    :ivar books_after: Directories under ``books/`` after the command.
    """

    old_books: tuple[str, ...] | None
    new_books: tuple[str, ...]
    exit_code: int
    says: str
    books_after: tuple[str, ...]


@pytest.fixture
def fx_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the settings at a data directory of the test, away from any ``.env`` of the working directory.

    :param tmp_path: Temporary directory of the test, the working directory and the data directory.
    :type tmp_path: Path
    :param monkeypatch: Sets the variables of the settings and the working directory, undone after the test.
    :type monkeypatch: pytest.MonkeyPatch
    :returns: The storage root the command works on.
    :rtype: Path
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(DATA_DIR_VARIABLE, str(tmp_path))
    monkeypatch.setenv(SECRET_VARIABLE, SECRET)
    return tmp_path / STORAGE


class TestMoveBookDirectories:
    """Tests for ``bookreviver-migrate move-book-directories``."""

    @pytest.mark.parametrize(
        'case',
        [
            pytest.param(
                MoveCase(
                    old_books=(BOOK, OTHER_BOOK),
                    new_books=(),
                    exit_code=0,
                    says='Moved 2 book directories',
                    books_after=(BOOK, OTHER_BOOK),
                ),
                id='moves-every-book',
            ),
            pytest.param(
                MoveCase(old_books=None, new_books=(), exit_code=0, says='nothing to move', books_after=()),
                id='nothing-to-move',
            ),
            pytest.param(
                MoveCase(
                    old_books=(BOOK,),
                    new_books=(BOOK,),
                    exit_code=1,
                    says='nothing was moved',
                    books_after=(BOOK,),
                ),
                id='refused-when-a-name-is-taken',
            ),
        ],
    )
    def test_moves_the_books_or_says_why_not(self, fx_storage: Path, case: MoveCase) -> None:
        """Verify the command moves the books, answers a root with nothing to move, and refuses a taken name.

        :param fx_storage: Storage root of the test, which the settings of the command name.
        :type fx_storage: Path
        :param case: The storage root before the command and its expected answer.
        :type case: MoveCase
        """
        for name in case.old_books or ():
            (fx_storage / OLD_ROOT / name).mkdir(parents=True)
        for name in case.new_books:
            (fx_storage / NEW_ROOT / name).mkdir(parents=True)

        result = CliRunner().invoke(migrate, [MOVE_COMMAND])

        books = fx_storage / NEW_ROOT
        books_after = tuple(sorted(path.name for path in books.iterdir())) if books.is_dir() else ()
        expect(result.exit_code == case.exit_code, result.output)
        expect(case.says in result.output, result.output)
        expect(books_after == case.books_after, f'The books are {books_after}.')
        assert_expectations()
