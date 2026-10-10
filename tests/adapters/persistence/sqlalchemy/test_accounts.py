"""Tests for the account tables and the two classes that implement the fastapi-users database protocols over them."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, override
from uuid import UUID, uuid4

import pytest
from delayed_assert import assert_expectations, expect
from sqlalchemy import func, select

from bookreviver.adapters.persistence.sqlalchemy.accounts import (
    AccessTokenDatabase,
    AccessTokenTable,
    AccountDatabase,
    AccountTable,
    OAuthAccountTable,
)
from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from bookreviver.app.container import build_container
from bookreviver.app.settings import PersistenceBackend
from bookreviver.ports.persistence import NoChangeOpenError, UnitOfWork
from tests.helpers.schema import create_schema

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from dishka import AsyncContainer
    from sqlalchemy.ext.asyncio import AsyncSession

    from bookreviver.app.settings import Settings

pytestmark = pytest.mark.anyio

EMAIL: str = 'Reader@Example.org'
PASSWORD_HASH: str = 'hash-of-a-password'
NEW_HASH: str = 'new-hash'
PROVIDER: str = 'google'
PROVIDER_ACCOUNT: str = 'provider-account-1'
PROVIDER_EMAIL: str = 'reader@gmail.example'
ACCESS_TOKEN: str = 'provider-access-token'
RENEWED_TOKEN: str = 'renewed'
RENEWED_EXPIRY: int = 1_900_000_000
SESSION_TOKEN: str = 't' * 43
OTHER_TOKEN: str = 'u' * 43
# The time a stored session token was created, and the step the tests move a limit away from it
CREATED_AT: datetime = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
STEP: timedelta = timedelta(seconds=1)
# How many times a write opens a block of the unit of work
ONE_BLOCK: int = 1
# The columns a test gives a new user and a linked provider account, and the new values of an update of each
NEW_USER: dict[str, Any] = {'email': EMAIL, 'hashed_password': PASSWORD_HASH}
LINK: dict[str, Any] = {
    'oauth_name': PROVIDER,
    'access_token': ACCESS_TOKEN,
    'account_id': PROVIDER_ACCOUNT,
    'account_email': PROVIDER_EMAIL,
    'expires_at': None,
    'refresh_token': None,
}
VERIFIED: dict[str, Any] = {'is_verified': True, 'hashed_password': NEW_HASH}
DEACTIVATED: dict[str, Any] = {'is_active': False}
RENEWED_LINK: dict[str, Any] = {'access_token': RENEWED_TOKEN, 'expires_at': RENEWED_EXPIRY}
LATER: dict[str, Any] = {'created_at': CREATED_AT + STEP}


class CountingUnitOfWork(SqlAlchemyUnitOfWork):
    """A unit of work that counts how many ``change`` blocks are opened on it.

    :ivar blocks: Number of ``change`` blocks opened so far.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repositories to ``session`` and start counting.

        :param session: Session of the test.
        :type session: AsyncSession
        """
        super().__init__(session)
        self.blocks = 0

    @override
    @asynccontextmanager
    async def change(self) -> AsyncIterator[None]:
        """Count the block, then open it as the unit of work does.

        :returns: Iterator yielding while the block is open.
        :rtype: AsyncIterator[None]
        """
        self.blocks += 1
        async with super().change():
            yield


@pytest.fixture
async def fx_container(fx_settings: Settings) -> AsyncIterator[AsyncContainer]:
    """Yield the application's container on the SQL backend, over a database with every table created.

    :param fx_settings: Settings pointing at a fresh data directory of the test.
    :type fx_settings: Settings
    :returns: Iterator yielding the container and closing it afterwards.
    :rtype: AsyncIterator[AsyncContainer]
    """
    await create_schema(fx_settings)
    container = build_container(fx_settings.model_copy(update={'persistence': PersistenceBackend.SQLALCHEMY}))
    yield container
    await container.close()


@pytest.fixture
async def fx_database(fx_container: AsyncContainer) -> SqlDatabase:
    """Return the database of the container.

    :param fx_container: The application's container on the SQL backend.
    :type fx_container: AsyncContainer
    :returns: The open database.
    :rtype: SqlDatabase
    """
    return await fx_container.get(SqlDatabase)


@pytest.fixture
async def fx_session(fx_database: SqlDatabase) -> AsyncIterator[AsyncSession]:
    """Open the session of one request.

    :param fx_database: Database with every table created.
    :type fx_database: SqlDatabase
    :returns: Iterator yielding the session and closing it afterwards.
    :rtype: AsyncIterator[AsyncSession]
    """
    async with fx_database.sessions() as session:
        yield session


@pytest.fixture
def fx_uow(fx_session: AsyncSession) -> CountingUnitOfWork:
    """Build the unit of work of the request, which guards its session.

    :param fx_session: Session of the request.
    :type fx_session: AsyncSession
    :returns: The unit of work, counting the blocks opened on it.
    :rtype: CountingUnitOfWork
    """
    return CountingUnitOfWork(fx_session)


@pytest.fixture
def fx_users(fx_session: AsyncSession, fx_uow: CountingUnitOfWork) -> AccountDatabase:
    """Build the user database of the request.

    :param fx_session: Session of the request.
    :type fx_session: AsyncSession
    :param fx_uow: Unit of work of the request.
    :type fx_uow: CountingUnitOfWork
    :returns: The user database over the session and the unit of work.
    :rtype: AccountDatabase
    """
    return AccountDatabase(fx_session, fx_uow)


@pytest.fixture
def fx_tokens(fx_session: AsyncSession, fx_uow: CountingUnitOfWork) -> AccessTokenDatabase:
    """Build the session token database of the request.

    :param fx_session: Session of the request.
    :type fx_session: AsyncSession
    :param fx_uow: Unit of work of the request.
    :type fx_uow: CountingUnitOfWork
    :returns: The token database over the session and the unit of work.
    :rtype: AccessTokenDatabase
    """
    return AccessTokenDatabase(fx_session, fx_uow)


@pytest.fixture
async def fx_user(fx_users: AccountDatabase, fx_uow: CountingUnitOfWork) -> AccountTable:
    """Store a user and forget the blocks that stored it.

    :param fx_users: The user database of the request.
    :type fx_users: AccountDatabase
    :param fx_uow: Unit of work of the request, whose count of blocks is reset.
    :type fx_uow: CountingUnitOfWork
    :returns: The stored user.
    :rtype: AccountTable
    """
    user = await fx_users.create(dict(NEW_USER))
    fx_uow.blocks = 0
    return user


@pytest.fixture
async def fx_linked_user(fx_users: AccountDatabase, fx_user: AccountTable, fx_uow: CountingUnitOfWork) -> AccountTable:
    """Link a provider account to the stored user and forget the blocks that did it.

    :param fx_users: The user database of the request.
    :type fx_users: AccountDatabase
    :param fx_user: The stored user.
    :type fx_user: AccountTable
    :param fx_uow: Unit of work of the request, whose count of blocks is reset.
    :type fx_uow: CountingUnitOfWork
    :returns: The user, with the provider account in ``oauth_accounts``.
    :rtype: AccountTable
    """
    linked = await fx_users.add_oauth_account(fx_user, dict(LINK))
    fx_uow.blocks = 0
    return linked


async def _count(database: SqlDatabase, table: type[OAuthAccountTable | AccessTokenTable]) -> int:
    """Count the rows of a table in a session of its own, which sees only what was committed.

    :param database: Database to read.
    :type database: SqlDatabase
    :param table: Table to count.
    :type table: type[OAuthAccountTable] | type[AccessTokenTable]
    :returns: Number of committed rows.
    :rtype: int
    """
    async with database.sessions() as session:
        return await session.scalar(select(func.count()).select_from(table)) or 0


async def _committed_user(database: SqlDatabase, user_id: UUID) -> AccountTable | None:
    """Read a user in a session of its own, which sees only what was committed.

    :param database: Database to read.
    :type database: SqlDatabase
    :param user_id: Identifier of the user.
    :type user_id: UUID
    :returns: The user with its provider accounts, or None when none is committed.
    :rtype: AccountTable | None
    """
    async with database.sessions() as session:
        return await AccountDatabase(session, CountingUnitOfWork(session)).get(user_id)


async def _committed_token(database: SqlDatabase) -> AccessTokenTable | None:
    """Read the session token of the tests in a session of its own, which sees only what was committed.

    :param database: Database to read.
    :type database: SqlDatabase
    :returns: The token, or None when none is committed.
    :rtype: AccessTokenTable | None
    """
    async with database.sessions() as session:
        return await AccessTokenDatabase(session, CountingUnitOfWork(session)).get_by_token(SESSION_TOKEN)


def _token(user: AccountTable, **columns: Any) -> dict[str, Any]:
    """Build the columns of a session token of ``user``.

    :param user: The user the token is for.
    :type user: AccountTable
    :param columns: Columns that replace or extend the defaults, such as the time the token was created.
    :type columns: Any
    :returns: The token's columns, with ``SESSION_TOKEN`` as its value unless ``columns`` gives another.
    :rtype: dict[str, Any]
    """
    return {'token': SESSION_TOKEN, 'user_id': user.id, **columns}


class TestAccountDatabase:
    """Tests for the user database: its reads, its writes, and the block every write runs in."""

    async def test_create_stores_the_user_with_its_defaults(
        self, fx_users: AccountDatabase, fx_database: SqlDatabase
    ) -> None:
        """Verify a created user has an identifier, the defaults of a new account and no provider account, committed.

        :param fx_users: The user database of the request.
        :type fx_users: AccountDatabase
        :param fx_database: Database with every table created.
        :type fx_database: SqlDatabase
        """
        user = await fx_users.create(dict(NEW_USER))
        stored = await _committed_user(fx_database, user.id)
        expect(isinstance(user.id, UUID))
        expect((user.is_active, user.is_superuser, user.is_verified) == (True, False, False))
        expect(user.oauth_accounts == [])
        expect(stored is not None and stored.email == EMAIL)
        assert_expectations()

    @pytest.mark.parametrize('is_stored', [True, False], ids=['stored-identifier', 'unknown-identifier'])
    async def test_get_finds_a_user_by_its_identifier(
        self, fx_users: AccountDatabase, fx_user: AccountTable, *, is_stored: bool
    ) -> None:
        """Verify ``get`` returns the stored user for its identifier and None for another.

        :param fx_users: The user database of the request.
        :type fx_users: AccountDatabase
        :param fx_user: The stored user.
        :type fx_user: AccountTable
        :param is_stored: Whether to look for the stored user or for an identifier that nobody has.
        :type is_stored: bool
        """
        looked_up = await fx_users.get(fx_user.id if is_stored else uuid4())
        assert (looked_up is not None and looked_up.id == fx_user.id) if is_stored else looked_up is None

    @pytest.mark.parametrize(
        ('spelling', 'is_matched'),
        [
            pytest.param(EMAIL, True, id='as-stored'),
            pytest.param(EMAIL.lower(), True, id='lower-case'),
            pytest.param(EMAIL.upper(), True, id='upper-case'),
            pytest.param('other@example.org', False, id='another-address'),
        ],
    )
    async def test_get_by_email_ignores_the_case_of_the_address(
        self, fx_users: AccountDatabase, fx_user: AccountTable, spelling: str, *, is_matched: bool
    ) -> None:
        """Verify an address matches whatever the case of the stored address and of the one asked for.

        :param fx_users: The user database of the request.
        :type fx_users: AccountDatabase
        :param fx_user: The stored user, registered as ``Reader@Example.org``.
        :type fx_user: AccountTable
        :param spelling: The address to look for.
        :type spelling: str
        :param is_matched: Whether the stored user is expected.
        :type is_matched: bool
        """
        looked_up = await fx_users.get_by_email(spelling)
        assert (looked_up is not None and looked_up.id == fx_user.id) if is_matched else looked_up is None

    @pytest.mark.parametrize(
        ('provider', 'account', 'is_linked'),
        [
            pytest.param(PROVIDER, PROVIDER_ACCOUNT, True, id='linked'),
            pytest.param('facebook', PROVIDER_ACCOUNT, False, id='another-provider'),
            pytest.param(PROVIDER, 'provider-account-2', False, id='another-account'),
        ],
    )
    async def test_get_by_oauth_account_finds_the_linked_user(
        self,
        fx_users: AccountDatabase,
        fx_linked_user: AccountTable,
        provider: str,
        account: str,
        *,
        is_linked: bool,
    ) -> None:
        """Verify a user is found by the provider and the account the provider knows it by, and by nothing else.

        :param fx_users: The user database of the request.
        :type fx_users: AccountDatabase
        :param fx_linked_user: The stored user with a Google account linked.
        :type fx_linked_user: AccountTable
        :param provider: Name of the provider to look under.
        :type provider: str
        :param account: Identifier of the account at the provider.
        :type account: str
        :param is_linked: Whether the linked user is expected.
        :type is_linked: bool
        """
        looked_up = await fx_users.get_by_oauth_account(provider, account)
        assert (looked_up is not None and looked_up.id == fx_linked_user.id) if is_linked else looked_up is None

    async def test_update_changes_the_columns_and_commits_them(
        self, fx_users: AccountDatabase, fx_user: AccountTable, fx_database: SqlDatabase
    ) -> None:
        """Verify an update returns the user with the new values, and another session reads them.

        :param fx_users: The user database of the request.
        :type fx_users: AccountDatabase
        :param fx_user: The stored user.
        :type fx_user: AccountTable
        :param fx_database: Database with every table created.
        :type fx_database: SqlDatabase
        """
        updated = await fx_users.update(fx_user, dict(VERIFIED))
        stored = await _committed_user(fx_database, fx_user.id)
        expect(updated.is_verified is True)
        expect(stored is not None and (stored.is_verified, stored.hashed_password) == (True, NEW_HASH))
        assert_expectations()

    async def test_add_oauth_account_links_the_provider_account(
        self, fx_linked_user: AccountTable, fx_database: SqlDatabase
    ) -> None:
        """Verify linking returns the user with the account in ``oauth_accounts``, and the account is committed.

        :param fx_linked_user: The stored user with a Google account linked.
        :type fx_linked_user: AccountTable
        :param fx_database: Database with every table created.
        :type fx_database: SqlDatabase
        """
        stored = await _committed_user(fx_database, fx_linked_user.id)
        [link] = fx_linked_user.oauth_accounts
        expect((link.oauth_name, link.account_id, link.account_email) == (PROVIDER, PROVIDER_ACCOUNT, PROVIDER_EMAIL))
        expect(link.user_id == fx_linked_user.id)
        expect(stored is not None and [account.id for account in stored.oauth_accounts] == [link.id])
        assert_expectations()

    async def test_update_oauth_account_changes_the_linked_account(
        self, fx_users: AccountDatabase, fx_linked_user: AccountTable, fx_database: SqlDatabase
    ) -> None:
        """Verify an update of the linked account returns the user and commits the new token and expiry.

        :param fx_users: The user database of the request.
        :type fx_users: AccountDatabase
        :param fx_linked_user: The stored user with a Google account linked.
        :type fx_linked_user: AccountTable
        :param fx_database: Database with every table created.
        :type fx_database: SqlDatabase
        """
        [link] = fx_linked_user.oauth_accounts
        returned = await fx_users.update_oauth_account(fx_linked_user, link, dict(RENEWED_LINK))
        stored = await _committed_user(fx_database, fx_linked_user.id)
        expect(returned is fx_linked_user)
        expect(stored is not None and stored.oauth_accounts[0].access_token == RENEWED_TOKEN)
        expect(stored is not None and stored.oauth_accounts[0].expires_at == RENEWED_EXPIRY)
        assert_expectations()

    async def test_delete_removes_the_user_with_its_provider_accounts_and_tokens(
        self,
        fx_users: AccountDatabase,
        fx_tokens: AccessTokenDatabase,
        fx_linked_user: AccountTable,
        fx_database: SqlDatabase,
    ) -> None:
        """Verify deleting a user takes its provider accounts and session tokens with it, as the keys cascade.

        :param fx_users: The user database of the request.
        :type fx_users: AccountDatabase
        :param fx_tokens: The session token database of the request.
        :type fx_tokens: AccessTokenDatabase
        :param fx_linked_user: The stored user with a Google account linked.
        :type fx_linked_user: AccountTable
        :param fx_database: Database with every table created.
        :type fx_database: SqlDatabase
        """
        await fx_tokens.create(_token(fx_linked_user))
        user_id = fx_linked_user.id
        await fx_users.delete(fx_linked_user)
        expect(await _committed_user(fx_database, user_id) is None)
        expect(await _count(fx_database, OAuthAccountTable) == 0)
        expect(await _count(fx_database, AccessTokenTable) == 0)
        assert_expectations()

    @pytest.mark.parametrize(
        'write',
        [
            pytest.param(lambda users, _user: users.create(dict(NEW_USER, email='new@example.org')), id='create-user'),
            pytest.param(lambda users, user: users.update(user, dict(DEACTIVATED)), id='update-user'),
            pytest.param(lambda users, user: users.delete(user), id='delete-user'),
            pytest.param(lambda users, user: users.add_oauth_account(user, dict(LINK)), id='add-oauth-account'),
        ],
    )
    async def test_every_write_runs_in_one_change_block(
        self,
        fx_users: AccountDatabase,
        fx_user: AccountTable,
        fx_uow: CountingUnitOfWork,
        write: Callable[[AccountDatabase, AccountTable], Awaitable[Any]],
    ) -> None:
        """Verify each write opens exactly one ``change`` block of the unit of work, so it takes the write lock.

        :param fx_users: The user database of the request.
        :type fx_users: AccountDatabase
        :param fx_user: The stored user, with the count of blocks reset after it was stored.
        :type fx_user: AccountTable
        :param fx_uow: Unit of work of the request, counting its blocks.
        :type fx_uow: CountingUnitOfWork
        :param write: The write to run, given the user database and the stored user.
        :type write: Callable[[AccountDatabase, AccountTable], Awaitable[Any]]
        """
        await write(fx_users, fx_user)
        assert fx_uow.blocks == ONE_BLOCK

    async def test_update_of_the_linked_account_runs_in_one_change_block(
        self, fx_users: AccountDatabase, fx_linked_user: AccountTable, fx_uow: CountingUnitOfWork
    ) -> None:
        """Verify updating the provider account opens exactly one ``change`` block.

        :param fx_users: The user database of the request.
        :type fx_users: AccountDatabase
        :param fx_linked_user: The stored user with a Google account linked.
        :type fx_linked_user: AccountTable
        :param fx_uow: Unit of work of the request, counting its blocks.
        :type fx_uow: CountingUnitOfWork
        """
        [link] = fx_linked_user.oauth_accounts
        await fx_users.update_oauth_account(fx_linked_user, link, dict(RENEWED_LINK))
        assert fx_uow.blocks == ONE_BLOCK

    async def test_write_outside_a_block_is_refused(self, fx_session: AsyncSession, fx_uow: CountingUnitOfWork) -> None:
        """Verify a user added to the session without a block cannot be flushed, as the guard of the unit of work says.

        :param fx_session: Session of the request.
        :type fx_session: AsyncSession
        :param fx_uow: Unit of work of the request, which guards the session from its construction.
        :type fx_uow: CountingUnitOfWork
        """
        fx_session.add(AccountTable(email='stray@example.org', hashed_password=''))
        with pytest.raises(NoChangeOpenError):
            await fx_session.flush()
        assert fx_uow.blocks == 0


class TestAccessTokenDatabase:
    """Tests for the session token database: its reads, its writes, and the block every write runs in."""

    @staticmethod
    async def _store(tokens: AccessTokenDatabase, user: AccountTable, uow: CountingUnitOfWork) -> AccessTokenTable:
        """Store a token created at ``CREATED_AT`` and forget the blocks that stored it.

        :param tokens: The session token database of the request.
        :type tokens: AccessTokenDatabase
        :param user: The user the token is for.
        :type user: AccountTable
        :param uow: Unit of work of the request, whose count of blocks is reset.
        :type uow: CountingUnitOfWork
        :returns: The stored token.
        :rtype: AccessTokenTable
        """
        stored = await tokens.create(_token(user, created_at=CREATED_AT))
        uow.blocks = 0
        return stored

    async def test_create_stores_a_token_created_now_in_utc(
        self, fx_tokens: AccessTokenDatabase, fx_user: AccountTable, fx_database: SqlDatabase
    ) -> None:
        """Verify a token created without a time gets the current time with its time zone, and is committed.

        :param fx_tokens: The session token database of the request.
        :type fx_tokens: AccessTokenDatabase
        :param fx_user: The stored user the token is for.
        :type fx_user: AccountTable
        :param fx_database: Database with every table created.
        :type fx_database: SqlDatabase
        """
        before = datetime.now(UTC)
        token = await fx_tokens.create(_token(fx_user))
        stored = await _committed_token(fx_database)
        expect(before <= token.created_at <= datetime.now(UTC))
        expect(token.created_at.utcoffset() == timedelta(0))
        expect(stored is not None and stored.created_at == token.created_at)
        assert_expectations()

    @pytest.mark.parametrize('value', [SESSION_TOKEN, OTHER_TOKEN], ids=['stored-token', 'unknown-token'])
    async def test_get_by_token_finds_only_the_stored_token(
        self,
        fx_tokens: AccessTokenDatabase,
        fx_user: AccountTable,
        fx_uow: CountingUnitOfWork,
        value: str,
    ) -> None:
        """Verify the stored token is found by its value and another value is not.

        :param fx_tokens: The session token database of the request.
        :type fx_tokens: AccessTokenDatabase
        :param fx_user: The stored user the token is for.
        :type fx_user: AccountTable
        :param fx_uow: Unit of work of the request.
        :type fx_uow: CountingUnitOfWork
        :param value: The value to look up.
        :type value: str
        """
        await self._store(fx_tokens, fx_user, fx_uow)
        looked_up = await fx_tokens.get_by_token(value)
        assert (looked_up is not None) == (value == SESSION_TOKEN)

    @pytest.mark.parametrize(
        ('max_age', 'is_within_age'),
        [
            pytest.param(None, True, id='no-limit'),
            pytest.param(CREATED_AT - STEP, True, id='limit-before-creation'),
            pytest.param(CREATED_AT, True, id='limit-at-creation'),
            pytest.param(CREATED_AT + STEP, False, id='limit-after-creation'),
        ],
    )
    async def test_get_by_token_does_not_find_a_token_older_than_max_age(
        self,
        fx_tokens: AccessTokenDatabase,
        fx_user: AccountTable,
        fx_uow: CountingUnitOfWork,
        max_age: datetime | None,
        *,
        is_within_age: bool,
    ) -> None:
        """Verify a token created before ``max_age`` is not found, and one created at or after it is.

        :param fx_tokens: The session token database of the request.
        :type fx_tokens: AccessTokenDatabase
        :param fx_user: The stored user the token is for.
        :type fx_user: AccountTable
        :param fx_uow: Unit of work of the request.
        :type fx_uow: CountingUnitOfWork
        :param max_age: Earliest creation time the token may have, or None for no limit.
        :type max_age: datetime | None
        :param is_within_age: Whether the token is expected.
        :type is_within_age: bool
        """
        await self._store(fx_tokens, fx_user, fx_uow)
        assert (await fx_tokens.get_by_token(SESSION_TOKEN, max_age) is not None) == is_within_age

    async def test_update_changes_the_columns_and_commits_them(
        self,
        fx_tokens: AccessTokenDatabase,
        fx_user: AccountTable,
        fx_uow: CountingUnitOfWork,
        fx_database: SqlDatabase,
    ) -> None:
        """Verify an update returns the token with the new time, and another session reads it.

        :param fx_tokens: The session token database of the request.
        :type fx_tokens: AccessTokenDatabase
        :param fx_user: The stored user the token is for.
        :type fx_user: AccountTable
        :param fx_uow: Unit of work of the request.
        :type fx_uow: CountingUnitOfWork
        :param fx_database: Database with every table created.
        :type fx_database: SqlDatabase
        """
        token = await self._store(fx_tokens, fx_user, fx_uow)
        updated = await fx_tokens.update(token, dict(LATER))
        stored = await _committed_token(fx_database)
        expect(updated.created_at == CREATED_AT + STEP)
        expect(stored is not None and stored.created_at == CREATED_AT + STEP)
        assert_expectations()

    async def test_delete_removes_the_token(
        self,
        fx_tokens: AccessTokenDatabase,
        fx_user: AccountTable,
        fx_uow: CountingUnitOfWork,
        fx_database: SqlDatabase,
    ) -> None:
        """Verify a deleted token is no longer found, which is how signing out revokes a session.

        :param fx_tokens: The session token database of the request.
        :type fx_tokens: AccessTokenDatabase
        :param fx_user: The stored user the token is for.
        :type fx_user: AccountTable
        :param fx_uow: Unit of work of the request.
        :type fx_uow: CountingUnitOfWork
        :param fx_database: Database with every table created.
        :type fx_database: SqlDatabase
        """
        token = await self._store(fx_tokens, fx_user, fx_uow)
        await fx_tokens.delete(token)
        expect(await fx_tokens.get_by_token(SESSION_TOKEN) is None)
        expect(await _count(fx_database, AccessTokenTable) == 0)
        assert_expectations()

    @pytest.mark.parametrize(
        'operation',
        [
            pytest.param(
                lambda tokens, _stored, user: tokens.create(_token(user, token=OTHER_TOKEN)), id='create-token'
            ),
            pytest.param(lambda tokens, stored, _user: tokens.update(stored, dict(LATER)), id='update-token'),
            pytest.param(lambda tokens, stored, _user: tokens.delete(stored), id='delete-token'),
        ],
    )
    async def test_every_write_runs_in_one_change_block(
        self,
        fx_tokens: AccessTokenDatabase,
        fx_user: AccountTable,
        fx_uow: CountingUnitOfWork,
        operation: Callable[[AccessTokenDatabase, AccessTokenTable, AccountTable], Awaitable[Any]],
    ) -> None:
        """Verify each write opens exactly one ``change`` block of the unit of work, so it takes the write lock.

        :param fx_tokens: The session token database of the request.
        :type fx_tokens: AccessTokenDatabase
        :param fx_user: The stored user the token is for.
        :type fx_user: AccountTable
        :param fx_uow: Unit of work of the request, counting its blocks.
        :type fx_uow: CountingUnitOfWork
        :param operation: The write to run, given the token database, a stored token and the user.
        :type operation: Callable[[AccessTokenDatabase, AccessTokenTable, AccountTable], Awaitable[Any]]
        """
        token = await self._store(fx_tokens, fx_user, fx_uow)
        await operation(fx_tokens, token, fx_user)
        assert fx_uow.blocks == ONE_BLOCK


class TestWiring:
    """Tests for the providers that hand the account classes the unit of work of the request."""

    async def test_the_books_and_the_accounts_share_one_unit_of_work_per_request(
        self, fx_container: AsyncContainer
    ) -> None:
        """Verify a request has one unit of work, whichever of its two names asks, since two would guard each other out.

        :param fx_container: The application's container on the SQL backend.
        :type fx_container: AsyncContainer
        """
        async with fx_container() as scope:
            port = await scope.get(UnitOfWork)
            adapter = await scope.get(SqlAlchemyUnitOfWork)
        assert port is adapter

    async def test_the_accounts_get_an_sql_unit_of_work_when_the_books_are_in_memory(
        self, fx_settings: Settings
    ) -> None:
        """Verify the account classes get an SQL unit of work when the books are in memory, as the test suite runs them.

        :param fx_settings: Settings with in-memory persistence and a fresh data directory of the test.
        :type fx_settings: Settings
        """
        await create_schema(fx_settings)
        container = build_container(fx_settings)
        try:
            async with container() as scope:
                port = await scope.get(UnitOfWork)
                adapter = await scope.get(SqlAlchemyUnitOfWork)
            assert port is not adapter
        finally:
            await container.close()
