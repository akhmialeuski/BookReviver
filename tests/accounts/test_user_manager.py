"""Tests for the rules of the user manager that reach beyond the account tables."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users.password import PasswordHelper
from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher

from bookreviver.adapters.persistence.sqlalchemy.accounts import OAuthAccountTable
from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
from bookreviver.app.providers.accounts import UserManager
from tests.helpers.fakes_accounts import PASSWORD
from tests.helpers.seeding import commit_account

if TYPE_CHECKING:
    from dishka import AsyncContainer


pytestmark = pytest.mark.anyio

PROVIDER: str = 'google'
PROVIDER_ACCOUNT: str = 'provider-account-1'
PROVIDER_EMAIL: str = 'reader@gmail.example'
CHANGED_PROVIDER_EMAIL: str = 'changed@gmail.example'
# What the provider issued, which the sign-in uses and the catalogue never stores
ISSUED_TOKEN: str = 'provider-access-token'
ISSUED_REFRESH_TOKEN: str = 'provider-refresh-token'
ISSUED_EXPIRY: int = 1_900_000_000
# The columns of a link to a provider account, none of them a token
LINK_COLUMNS: frozenset[str] = frozenset({'id', 'user_id', 'oauth_name', 'account_id', 'account_email'})


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


class TestOAuthCallback:
    """Tests for UserManager.oauth_callback(), which keeps the link to a provider account and none of its tokens."""

    async def test_first_sign_in_creates_the_account_and_a_link_without_tokens(
        self, fx_container: AsyncContainer
    ) -> None:
        """Verify a first sign-in stores a verified account and a link to the provider account, and no token.

        :param fx_container: The application's container.
        :type fx_container: AsyncContainer
        """
        async with fx_container() as scope:
            created = await (await scope.get(UserManager)).oauth_callback(
                PROVIDER,
                ISSUED_TOKEN,
                PROVIDER_ACCOUNT,
                PROVIDER_EMAIL,
                ISSUED_EXPIRY,
                ISSUED_REFRESH_TOKEN,
                is_verified_by_default=True,
            )
        async with fx_container() as scope:
            stored = await (await scope.get(UserManager)).get_by_oauth_account(PROVIDER, PROVIDER_ACCOUNT)

        [link] = stored.oauth_accounts
        expect(stored.id == created.id)
        expect((stored.email, stored.is_verified) == (PROVIDER_EMAIL, True))
        expect((link.oauth_name, link.account_id, link.account_email) == (PROVIDER, PROVIDER_ACCOUNT, PROVIDER_EMAIL))
        expect(set(OAuthAccountTable.__table__.columns.keys()) == LINK_COLUMNS)
        assert_expectations()

    async def test_second_sign_in_updates_the_existing_link(self, fx_container: AsyncContainer) -> None:
        """Verify a sign-in through a linked provider account signs in its account and updates the one link.

        :param fx_container: The application's container.
        :type fx_container: AsyncContainer
        """
        async with fx_container() as scope:
            first = await (await scope.get(UserManager)).oauth_callback(
                PROVIDER, ISSUED_TOKEN, PROVIDER_ACCOUNT, PROVIDER_EMAIL, is_verified_by_default=True
            )
        async with fx_container() as scope:
            second = await (await scope.get(UserManager)).oauth_callback(
                PROVIDER, ISSUED_TOKEN, PROVIDER_ACCOUNT, CHANGED_PROVIDER_EMAIL, is_verified_by_default=True
            )
        async with fx_container() as scope:
            stored = await (await scope.get(UserManager)).get_by_oauth_account(PROVIDER, PROVIDER_ACCOUNT)

        [link] = stored.oauth_accounts
        expect(second.id == first.id == stored.id)
        expect(stored.email == PROVIDER_EMAIL)
        expect(link.account_email == CHANGED_PROVIDER_EMAIL)
        assert_expectations()
