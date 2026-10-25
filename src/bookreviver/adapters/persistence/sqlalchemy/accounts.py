"""Account tables on advanced-alchemy's shared registry, and the fastapi-users protocols over them.

The tables ``user``, ``oauth_account`` and ``accesstoken`` are the ones fastapi-users reads, under the attribute names
it expects, and their identifiers and times have the types of every other table of the adapter. They are tables of
the catalogue, where ``oauth_account``, ``accesstoken`` and the library refer to ``user.id``. The recipe profiles live
in the database of each book.

The databases of fastapi-users run over the catalogue database and nothing else: every read is a ``read_session`` and
every write a ``change_session`` of
:class:`~bookreviver.adapters.persistence.sqlalchemy.catalogue_database.SqlAlchemyCatalogueDatabase`, so an account
write takes the same writer as every other write, and commits and rolls back for the caller. The session
of such a transaction is closed when it ends, and fastapi-users reads the rows after that, so every row is handed over
fully loaded, with its linked provider accounts, and detached. A row handed back to a database for an update is attached
to the session of that transaction again, and the sessions do not expire rows on commit, so the same object stays
usable.
"""

from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Any, override
from uuid import UUID, uuid4

from advanced_alchemy.base import DefaultBase
from fastapi_users.authentication.strategy.db import AccessTokenDatabase as AccessTokenDatabaseProtocol
from fastapi_users.db import BaseUserDatabase
from sqlalchemy import ForeignKey, String, UniqueConstraint, func, select
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bookreviver.adapters.persistence.sqlalchemy.columns import CASCADE

if TYPE_CHECKING:
    from fastapi_users.models import OAP
    from sqlalchemy import Select
    from sqlalchemy.orm import Session

    from bookreviver.adapters.persistence.sqlalchemy.catalogue_database import SqlAlchemyCatalogueDatabase

# The column the keys of the other account tables refer to
USER_TABLE: str = 'user'
USER_ID: str = f'{USER_TABLE}.id'
# ORM cascade of a user to its linked provider accounts; the deletion itself is left to the database
LINKED_ACCOUNT_CASCADE: str = 'all, delete'


class OAuthAccountTable(DefaultBase):
    """An account at a social sign-in provider linked to a user."""

    __tablename__ = 'oauth_account'
    # A provider names one of its accounts once, which the sign-in through the provider reads as one row
    __table_args__ = (UniqueConstraint('oauth_name', 'account_id'),)

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
        user_id: Mapped[UUID] = mapped_column(ForeignKey(USER_ID, ondelete=CASCADE), index=True)
        oauth_name: Mapped[str] = mapped_column(String(length=100), index=True)
        # fastapi-users declares the tokens of a link, and the application keeps none, so they are no columns
        access_token = ''
        expires_at = None
        refresh_token = None
        account_id: Mapped[str] = mapped_column(String(length=320), index=True)
        account_email: Mapped[str] = mapped_column(String(length=320))


class AccountTable(DefaultBase):
    """A user who signs in with a password or a social provider."""

    __tablename__ = USER_TABLE

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
        # fastapi-users reads the flag, and the application has no superuser, so it is no column and never set
        is_superuser = False
        is_verified: Mapped[bool] = mapped_column(default=False)
        # Loaded with the user, as fastapi-users reads them when a provider signs the user in
        oauth_accounts: Mapped[list[OAuthAccountTable]] = relationship(
            lazy='joined', cascade=LINKED_ACCOUNT_CASCADE, passive_deletes=True
        )


class AccessTokenTable(DefaultBase):
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
        user_id: Mapped[UUID] = mapped_column(ForeignKey(USER_ID, ondelete=CASCADE), index=True)


# The columns of the tables, for the conditions of a query, which the plain annotations above would type as values
USERS = AccountTable.__table__.c
OAUTH_ACCOUNTS = OAuthAccountTable.__table__.c
ACCESS_TOKENS = AccessTokenTable.__table__.c


def _store_row[RowT](session: Session, row: RowT, values: dict[str, Any]) -> RowT:
    """Set columns on an account row, write it in the transaction of the session, and load it again.

    A new row is added, and a row that an earlier transaction handed over, which is detached, is attached again, so the
    flush writes only the columns whose value changed. The row is read back, so it carries its defaults and, for a
    user, the current list of linked provider accounts.

    :param session: Session of a transaction that writes.
    :type session: Session
    :param row: A new row, or a loaded row of an earlier transaction.
    :type row: RowT
    :param values: New values by column, which may be empty.
    :type values: dict[str, Any]
    :returns: The same row, flushed and loaded.
    :rtype: RowT
    """
    for key, value in values.items():
        setattr(row, key, value)
    session.add(row)
    session.flush()
    session.refresh(row)
    return row


class AccountDatabase(BaseUserDatabase[AccountTable, UUID]):
    """Users and their linked provider accounts, read and written through the catalogue database.

    :ivar catalogue: Catalogue database whose sessions every read and every write of the users runs on.
    """

    def __init__(self, catalogue: SqlAlchemyCatalogueDatabase) -> None:
        """Read and write through ``catalogue``.

        :param catalogue: Catalogue database whose ``read_session`` and ``change_session`` run the transactions of the
            users.
        :type catalogue: SqlAlchemyCatalogueDatabase
        """
        self.catalogue = catalogue

    async def _one(self, statement: Select[AccountTable]) -> AccountTable | None:
        """Run ``statement`` and return its only user, loaded with the linked provider accounts.

        :param statement: Select of users.
        :type statement: Select[AccountTable]
        :returns: The user, or None when the statement finds none.
        :rtype: AccountTable | None
        """
        # The joined load of the provider accounts repeats the user once per account, which unique() folds
        return await self.catalogue.read_session(
            lambda session: session.execute(statement).unique().scalar_one_or_none()
        )

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
        return await self.catalogue.change_session(partial(_store_row, row=AccountTable(**create_dict), values={}))

    @override
    async def update(self, user: AccountTable, update_dict: dict[str, Any]) -> AccountTable:
        """Change columns of a stored user.

        :param user: User to change, as an earlier read returned it.
        :type user: AccountTable
        :param update_dict: New values by column.
        :type update_dict: dict[str, Any]
        :returns: The user as stored.
        :rtype: AccountTable
        """
        return await self.catalogue.change_session(partial(_store_row, row=user, values=update_dict))

    @override
    async def delete(self, user: AccountTable) -> None:
        """Remove a user with its provider accounts and session tokens.

        :param user: User to remove, as an earlier read returned it.
        :type user: AccountTable
        """
        await self.catalogue.change_session(lambda session: session.delete(user))

    # fastapi-users 15.0.5 declares ``UserOAuthProtocol`` with ``Generic`` instead of ``Protocol`` in its bases
    # (fastapi_users/models.py:33), so by PEP 544 it is a plain class, and ``UOAP``, which it bounds, accepts only its
    # subclasses. ``AccountTable`` has every member it asks for but does not inherit it, so ty refuses these overrides
    # of the two methods bound to ``UOAP``. Once fastapi-users declares it a protocol, ty reports the ignores as unused.
    @override
    async def add_oauth_account(self, user: AccountTable, create_dict: dict[str, Any]) -> AccountTable:  # ty: ignore[invalid-method-override]
        """Link a provider account to a user.

        :param user: User to link it to, as an earlier read returned it.
        :type user: AccountTable
        :param create_dict: Columns of the provider account.
        :type create_dict: dict[str, Any]
        :returns: The user, whose ``oauth_accounts`` holds the new link.
        :rtype: AccountTable
        """
        link = OAuthAccountTable(user_id=user.id, **create_dict)
        return await self.catalogue.change_session(partial(self._link, user=user, link=link))

    @staticmethod
    def _link(session: Session, *, user: AccountTable, link: OAuthAccountTable) -> AccountTable:
        """Store a provider account and load the list of provider accounts of its user again, with the new one.

        :param session: Session of a transaction that writes.
        :type session: Session
        :param user: User the provider account is linked to.
        :type user: AccountTable
        :param link: New provider account of the user.
        :type link: OAuthAccountTable
        :returns: The user, whose ``oauth_accounts`` holds the link.
        :rtype: AccountTable
        """
        _store_row(session, link, {})
        # The user is stored without a change, which loads it again with its list of provider accounts
        return _store_row(session, user, {})

    @override
    async def update_oauth_account(  # ty: ignore[invalid-method-override]
        self, user: AccountTable, oauth_account: OAP, update_dict: dict[str, Any]
    ) -> AccountTable:
        """Change columns of a provider account linked to a user.

        :param user: User the provider account is linked to.
        :type user: AccountTable
        :param oauth_account: Provider account to change, as an earlier read returned it.
        :type oauth_account: OAP
        :param update_dict: New values by column.
        :type update_dict: dict[str, Any]
        :returns: The user.
        :rtype: AccountTable
        """
        await self.catalogue.change_session(partial(_store_row, row=oauth_account, values=update_dict))
        return user


class AccessTokenDatabase(AccessTokenDatabaseProtocol[AccessTokenTable]):
    """Session tokens, read and written through the catalogue database.

    :ivar catalogue: Catalogue database whose sessions every read and every write of the tokens runs on.
    """

    def __init__(self, catalogue: SqlAlchemyCatalogueDatabase) -> None:
        """Read and write through ``catalogue``.

        :param catalogue: Catalogue database whose ``read_session`` and ``change_session`` run the transactions of the
            tokens.
        :type catalogue: SqlAlchemyCatalogueDatabase
        """
        self.catalogue = catalogue

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
        return await self.catalogue.read_session(lambda session: session.scalar(statement))

    @override
    async def create(self, create_dict: dict[str, Any]) -> AccessTokenTable:
        """Store a new token.

        :param create_dict: Columns of the token.
        :type create_dict: dict[str, Any]
        :returns: The stored token.
        :rtype: AccessTokenTable
        """
        return await self.catalogue.change_session(partial(_store_row, row=AccessTokenTable(**create_dict), values={}))

    @override
    async def update(self, access_token: AccessTokenTable, update_dict: dict[str, Any]) -> AccessTokenTable:
        """Change columns of a stored token.

        :param access_token: Token to change, as an earlier read returned it.
        :type access_token: AccessTokenTable
        :param update_dict: New values by column.
        :type update_dict: dict[str, Any]
        :returns: The token as stored.
        :rtype: AccessTokenTable
        """
        return await self.catalogue.change_session(partial(_store_row, row=access_token, values=update_dict))

    @override
    async def delete(self, access_token: AccessTokenTable) -> None:
        """Remove a token, which signs its session out.

        :param access_token: Token to remove, as an earlier read returned it.
        :type access_token: AccessTokenTable
        """
        await self.catalogue.change_session(lambda session: session.delete(access_token))
