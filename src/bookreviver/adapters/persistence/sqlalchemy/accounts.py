"""Account tables on advanced-alchemy's shared registry, and the fastapi-users protocols over them.

The tables ``user``, ``oauth_account`` and ``accesstoken`` are the ones fastapi-users reads, under the attribute names
it expects, and their identifiers and times have the types of every other table of the adapter. The foreign keys of
the books refer to ``user.id`` by name.

Reads go through the session of the request. Every write runs in the ``change`` block of the request's unit of work,
which takes the same write lock of SQLite as every other write, and commits and rolls back for the caller, so no
account write commits by itself. The tables derive from :class:`AccountRow`, which is a ``DefaultBase`` and so is
covered by the write guard of the unit of work, and which the unit of work leaves loaded when a block opens.
"""

from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Any, override
from uuid import UUID, uuid4

from advanced_alchemy.base import DefaultBase
from fastapi_users.authentication.strategy.db import AccessTokenDatabase as AccessTokenDatabaseProtocol
from fastapi_users.db import BaseUserDatabase
from sqlalchemy import ForeignKey, String, func, select
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from fastapi_users.models import OAP
    from sqlalchemy import Select
    from sqlalchemy.ext.asyncio import AsyncSession

    from bookreviver.ports.persistence import UnitOfWork

# The column the keys of the other account tables refer to
USER_ID: str = 'user.id'
# The referential action of the keys to ``user``, which the first revision of the schema wrote in lower case
CASCADE: str = 'cascade'
# ORM cascade of a user to its linked provider accounts; the deletion itself is left to the database
LINKED_ACCOUNT_CASCADE: str = 'all, delete'


class AccountRow(DefaultBase):
    """Base of the account tables, which a block of the unit of work does not expire.

    fastapi-users loads the account of a request before the route runs and reads it again after the route, and a lazy
    load of an expired row is impossible in an async session.
    """

    __abstract__ = True


class OAuthAccountTable(AccountRow):
    """An account at a social sign-in provider linked to a user."""

    __tablename__ = 'oauth_account'

    # Plain annotations for the type checkers, as in ``AccountTable``
    if TYPE_CHECKING:
        id: UUID
        user_id: UUID
        oauth_name: str
        access_token: str
        expires_at: int | None
        refresh_token: str | None
        account_id: str
        account_email: str
    else:
        id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
        user_id: Mapped[UUID] = mapped_column(ForeignKey(USER_ID, ondelete=CASCADE))
        oauth_name: Mapped[str] = mapped_column(String(length=100), index=True)
        access_token: Mapped[str] = mapped_column(String(length=1024))
        expires_at: Mapped[int | None]
        refresh_token: Mapped[str | None] = mapped_column(String(length=1024))
        account_id: Mapped[str] = mapped_column(String(length=320), index=True)
        account_email: Mapped[str] = mapped_column(String(length=320))


class AccountTable(AccountRow):
    """A user who signs in with a password or a social provider."""

    __tablename__ = 'user'

    # The type checkers read the plain annotations, which are what the ``UserOAuthProtocol`` of fastapi-users asks
    # for, because a ``Mapped`` attribute is a descriptor to them
    if TYPE_CHECKING:
        id: UUID
        email: str
        hashed_password: str
        is_active: bool
        is_superuser: bool
        is_verified: bool
        oauth_accounts: list[OAuthAccountTable]
    else:
        id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
        email: Mapped[str] = mapped_column(String(length=320), unique=True, index=True)
        hashed_password: Mapped[str] = mapped_column(String(length=1024))
        is_active: Mapped[bool] = mapped_column(default=True)
        is_superuser: Mapped[bool] = mapped_column(default=False)
        is_verified: Mapped[bool] = mapped_column(default=False)
        # Loaded with the user, as fastapi-users reads them when a provider signs the user in
        oauth_accounts: Mapped[list[OAuthAccountTable]] = relationship(
            lazy='joined', cascade=LINKED_ACCOUNT_CASCADE, passive_deletes=True
        )


class AccessTokenTable(AccountRow):
    """An opaque session token, stored so that signing out revokes it."""

    __tablename__ = 'accesstoken'

    # Plain annotations for the type checkers, as in ``AccountTable``
    if TYPE_CHECKING:
        token: str
        created_at: datetime
        user_id: UUID
    else:
        token: Mapped[str] = mapped_column(String(length=43), primary_key=True)
        created_at: Mapped[datetime] = mapped_column(index=True, default=partial(datetime.now, UTC))
        user_id: Mapped[UUID] = mapped_column(ForeignKey(USER_ID, ondelete=CASCADE))


# The columns of the tables, for the conditions of a query, which the plain annotations above would type as values
USERS = AccountTable.__table__.c
OAUTH_ACCOUNTS = OAuthAccountTable.__table__.c
ACCESS_TOKENS = AccessTokenTable.__table__.c


class AccountDatabase(BaseUserDatabase[AccountTable, UUID]):
    """Users and their linked provider accounts, read in the session of the request and written in its ``change``.

    :ivar session: Session of the current request, which every read goes through.
    :ivar uow: Unit of work of that session, whose ``change`` block every write runs in.
    """

    def __init__(self, session: AsyncSession, uow: UnitOfWork) -> None:
        """Read through ``session`` and write in the blocks of ``uow``.

        :param session: Session of the current request, the one ``uow`` guards.
        :type session: AsyncSession
        :param uow: Unit of work of that session, whose ``change`` block every write runs in.
        :type uow: UnitOfWork
        """
        self.session = session
        self.uow = uow

    async def _one(self, statement: Select[AccountTable]) -> AccountTable | None:
        """Run ``statement`` and return its only user, loaded with the linked provider accounts.

        :param statement: Select of users.
        :type statement: Select[AccountTable]
        :returns: The user, or None when the statement finds none.
        :rtype: AccountTable | None
        """
        # The joined load of the provider accounts repeats the user once per account, which unique() folds
        return (await self.session.execute(statement)).unique().scalar_one_or_none()

    @override
    async def get(self, id: UUID) -> AccountTable | None:
        """Return the user with this identifier.

        :param id: Identifier of the user.
        :type id: UUID
        :returns: The user, or None when none has it.
        :rtype: AccountTable | None
        """
        return await self._one(select(AccountTable).where(USERS.id == id))

    @override
    async def get_by_email(self, email: str) -> AccountTable | None:
        """Return the user with this address, whatever the case of either.

        :param email: Email address to look for.
        :type email: str
        :returns: The user, or None when none has the address.
        :rtype: AccountTable | None
        """
        return await self._one(select(AccountTable).where(func.lower(USERS.email) == func.lower(email)))

    @override
    async def get_by_oauth_account(self, oauth: str, account_id: str) -> AccountTable | None:
        """Return the user a provider account is linked to.

        :param oauth: Name of the provider.
        :type oauth: str
        :param account_id: Identifier of the account at the provider.
        :type account_id: str
        :returns: The user, or None when no user is linked to it.
        :rtype: AccountTable | None
        """
        statement = (
            select(AccountTable)
            .join(OAuthAccountTable)
            .where(OAUTH_ACCOUNTS.oauth_name == oauth, OAUTH_ACCOUNTS.account_id == account_id)
        )
        return await self._one(statement)

    @override
    async def create(self, create_dict: dict[str, Any]) -> AccountTable:
        """Store a new user.

        :param create_dict: Columns of the user.
        :type create_dict: dict[str, Any]
        :returns: The stored user, with its defaults and its empty list of provider accounts loaded.
        :rtype: AccountTable
        """
        user = AccountTable(**create_dict)
        async with self.uow.change():
            self.session.add(user)
        await self.session.refresh(user)
        return user

    @override
    async def update(self, user: AccountTable, update_dict: dict[str, Any]) -> AccountTable:
        """Change columns of a stored user.

        :param user: User to change.
        :type user: AccountTable
        :param update_dict: New values by column.
        :type update_dict: dict[str, Any]
        :returns: The user as stored.
        :rtype: AccountTable
        """
        async with self.uow.change():
            for key, value in update_dict.items():
                setattr(user, key, value)
            self.session.add(user)
        await self.session.refresh(user)
        return user

    @override
    async def delete(self, user: AccountTable) -> None:
        """Remove a user with its provider accounts and session tokens.

        :param user: User to remove.
        :type user: AccountTable
        """
        async with self.uow.change():
            await self.session.delete(user)

    # ty reads the ``UOAP`` of the two methods below as a parameter of the method and wants every user in and out, while
    # the base class binds it through the annotation of ``self`` to the user type of the class, which is how
    # fastapi-users and the other checkers read it
    @override
    async def add_oauth_account(self, user: AccountTable, create_dict: dict[str, Any]) -> AccountTable:  # ty: ignore[invalid-method-override]
        """Link a provider account to a user.

        :param user: User to link it to.
        :type user: AccountTable
        :param create_dict: Columns of the provider account.
        :type create_dict: dict[str, Any]
        :returns: The user, whose ``oauth_accounts`` holds the new link.
        :rtype: AccountTable
        """
        async with self.uow.change():
            self.session.add(OAuthAccountTable(user_id=user.id, **create_dict))
        # Loads the list of the user's provider accounts again, now with the new one
        await self.session.refresh(user)
        return user

    @override
    async def update_oauth_account(  # ty: ignore[invalid-method-override]
        self, user: AccountTable, oauth_account: OAP, update_dict: dict[str, Any]
    ) -> AccountTable:
        """Change columns of a provider account linked to a user.

        :param user: User the provider account is linked to.
        :type user: AccountTable
        :param oauth_account: Provider account to change.
        :type oauth_account: OAP
        :param update_dict: New values by column.
        :type update_dict: dict[str, Any]
        :returns: The user.
        :rtype: AccountTable
        """
        async with self.uow.change():
            for key, value in update_dict.items():
                setattr(oauth_account, key, value)
            self.session.add(oauth_account)
        return user


class AccessTokenDatabase(AccessTokenDatabaseProtocol[AccessTokenTable]):
    """Session tokens, read in the session of the request and written in its ``change``.

    :ivar session: Session of the current request, which every read goes through.
    :ivar uow: Unit of work of that session, whose ``change`` block every write runs in.
    """

    def __init__(self, session: AsyncSession, uow: UnitOfWork) -> None:
        """Read through ``session`` and write in the blocks of ``uow``.

        :param session: Session of the current request, the one ``uow`` guards.
        :type session: AsyncSession
        :param uow: Unit of work of that session, whose ``change`` block every write runs in.
        :type uow: UnitOfWork
        """
        self.session = session
        self.uow = uow

    @override
    async def get_by_token(self, token: str, max_age: datetime | None = None) -> AccessTokenTable | None:
        """Return the stored token, unless it is older than ``max_age``.

        :param token: Value of the token.
        :type token: str
        :param max_age: Earliest creation time the token may have, with its time zone, or None for no limit.
        :type max_age: datetime | None
        :returns: The token, or None when it is not stored or was created before ``max_age``.
        :rtype: AccessTokenTable | None
        """
        statement = select(AccessTokenTable).where(ACCESS_TOKENS.token == token)
        if max_age is not None:
            statement = statement.where(ACCESS_TOKENS.created_at >= max_age)
        return await self.session.scalar(statement)

    @override
    async def create(self, create_dict: dict[str, Any]) -> AccessTokenTable:
        """Store a new token.

        :param create_dict: Columns of the token.
        :type create_dict: dict[str, Any]
        :returns: The stored token.
        :rtype: AccessTokenTable
        """
        access_token = AccessTokenTable(**create_dict)
        async with self.uow.change():
            self.session.add(access_token)
        await self.session.refresh(access_token)
        return access_token

    @override
    async def update(self, access_token: AccessTokenTable, update_dict: dict[str, Any]) -> AccessTokenTable:
        """Change columns of a stored token.

        :param access_token: Token to change.
        :type access_token: AccessTokenTable
        :param update_dict: New values by column.
        :type update_dict: dict[str, Any]
        :returns: The token as stored.
        :rtype: AccessTokenTable
        """
        async with self.uow.change():
            for key, value in update_dict.items():
                setattr(access_token, key, value)
            self.session.add(access_token)
        await self.session.refresh(access_token)
        return access_token

    @override
    async def delete(self, access_token: AccessTokenTable) -> None:
        """Remove a token, which signs its session out.

        :param access_token: Token to remove.
        :type access_token: AccessTokenTable
        """
        async with self.uow.change():
            await self.session.delete(access_token)
