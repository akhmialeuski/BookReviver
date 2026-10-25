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
from tests.helpers.fakes_accounts import PASSWORD
from tests.helpers.seeding import commit_account

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from dishka import AsyncContainer

    from bookreviver.app.settings import Settings
    from tests.helpers.database import SharedDatabaseProvider

pytestmark = pytest.mark.anyio


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
