"""Tests for the rules of the user manager that reach beyond the account tables."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users.password import PasswordHelper
from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher

from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
from bookreviver.app.container import build_container
from bookreviver.app.providers.accounts import UserManager
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import AccountId
from bookreviver.ports.persistence import UnitOfWork
from bookreviver.ports.storage import AssetStore, SourceStore
from tests.helpers.builders import make_page, make_record
from tests.helpers.fakes_accounts import PASSWORD
from tests.helpers.seeding import commit_account, store_book
from tests.helpers.storage import BookFiles

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from dishka import AsyncContainer

    from bookreviver.app.settings import Settings
    from bookreviver.domain.entities import BookRecord
    from tests.helpers.database import SharedDatabaseProvider

pytestmark = pytest.mark.anyio

# More books than one window of the owner's book list, so a deletion has to page through them
OWNED_BOOKS: int = 3


@pytest.fixture
async def fx_container(
    fx_settings: Settings, fx_shared_database: SharedDatabaseProvider
) -> AsyncIterator[AsyncContainer]:
    """Yield the application's container on the catalogue and the books of the test, where the library is checked.

    :param fx_settings: Settings pointing at a fresh data directory of the test.
    :type fx_settings: Settings
    :param fx_shared_database: The catalogue and the books of the test, which the container is given in place of its
                               own.
    :type fx_shared_database: SharedDatabaseProvider
    :returns: Iterator yielding the container and closing it afterwards.
    :rtype: AsyncIterator[AsyncContainer]
    """
    container = build_container(fx_settings, [fx_shared_database])
    yield container
    await container.close()


async def _stored(container: AsyncContainer, book_record: BookRecord) -> bool:
    """Return whether the book is still stored.

    :param container: The application's container.
    :type container: AsyncContainer
    :param book_record: Record of the book to look up.
    :type book_record: BookRecord
    :returns: True when a new unit of work still finds the book.
    :rtype: bool
    """
    async with container() as scope:
        try:
            await (await scope.get(UnitOfWork)).read_book(
                book_record.id, lambda repos: repos.book_record.get(book_record.id)
            )
        except NotFoundError:
            return False
    return True


class TestDelete:
    """Tests for UserManager.delete(), which runs the on_before_delete hook first."""

    async def test_deleting_an_account_deletes_its_books_with_their_files(
        self, fx_container: AsyncContainer, fx_settings: Settings
    ) -> None:
        """Verify the account's books go with their rows and files, then the account, and another's stay.

        The owner key of a book refuses to delete an account that still owns one, so the books go first.

        :param fx_container: The application's container.
        :type fx_container: AsyncContainer
        :param fx_settings: Settings of the test, whose storage root the stores share.
        :type fx_settings: Settings
        """
        database = await fx_container.get(SqlDatabase)
        owner_id, neighbour_id = await commit_account(database), await commit_account(database)
        owned = [make_record(minutes=minute) for minute in range(OWNED_BOOKS)]
        kept = make_record()
        pages = {book_record.id: make_page() for book_record in (*owned, kept)}
        async with fx_container() as scope:
            uow = await scope.get(UnitOfWork)
            for book_record in (*owned, kept):
                owner = neighbour_id if book_record is kept else owner_id
                await store_book(uow, book_record, pages[book_record.id], owner=owner)
        files = BookFiles(
            sources=await fx_container.get(SourceStore),
            assets=await fx_container.get(AssetStore),
            root=fx_settings.storage_root,
        )
        for page in pages.values():
            await files.store(book_record.id, page)

        async with fx_container() as scope:
            manager = await scope.get(UserManager)
            await manager.delete(await manager.get(owner_id))

        async with fx_container() as scope:
            accounts = await scope.get(UserManager)
            expect(await accounts.user_db.get(AccountId(owner_id)) is None)
        expect([await _stored(fx_container, book_record) for book_record in owned] == [False] * OWNED_BOOKS)
        expect(all(files.gone(book_record.id) for book_record in owned))
        expect(await _stored(fx_container, kept))
        expect(files.kept(pages[kept.id]))
        assert_expectations()


class TestAuthenticate:
    """Tests for UserManager.authenticate(), which upgrades an outdated password hash after a successful check."""

    async def test_outdated_hash_is_replaced_after_sign_in(self, fx_container: AsyncContainer) -> None:
        """Verify signing in with a password stored as a bcrypt hash writes the argon2id hash of the same password.

        :param fx_container: The application's container.
        :type fx_container: AsyncContainer
        """
        outdated = PasswordHelper(PasswordHash((BcryptHasher(),))).hash(PASSWORD)
        account_id = await commit_account(await fx_container.get(SqlDatabase), hashed_password=outdated)

        async with fx_container() as scope:
            manager = await scope.get(UserManager)
            account = await manager.get(account_id)
            signed_in = await manager.authenticate(OAuth2PasswordRequestForm(username=account.email, password=PASSWORD))
        async with fx_container() as scope:
            stored = (await (await scope.get(UserManager)).get(account_id)).hashed_password

        current = PasswordHelper()
        expect(current.verify_and_update(PASSWORD, outdated)[1] is not None)
        expect(signed_in is not None)
        expect(stored != outdated)
        expect(current.verify_and_update(PASSWORD, stored) == (True, None))
        assert_expectations()
