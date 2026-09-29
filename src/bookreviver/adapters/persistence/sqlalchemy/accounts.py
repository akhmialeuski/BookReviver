"""Account tables owned by fastapi-users, on advanced-alchemy's shared registry, and their database adapters.

The base classes fix the table names ``user``, ``oauth_account`` and ``accesstoken``, because their foreign keys
refer to ``user.id`` by name.
"""

from typing import TYPE_CHECKING
from uuid import UUID

from advanced_alchemy.base import AdvancedDeclarativeBase
from fastapi_users_db_sqlalchemy import (
    SQLAlchemyBaseOAuthAccountTableUUID,
    SQLAlchemyBaseUserTableUUID,
    SQLAlchemyUserDatabase,
)
from fastapi_users_db_sqlalchemy.access_token import SQLAlchemyAccessTokenDatabase, SQLAlchemyBaseAccessTokenTableUUID
from sqlalchemy.orm import relationship

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class OAuthAccountTable(SQLAlchemyBaseOAuthAccountTableUUID, AdvancedDeclarativeBase):
    """An account at a social sign-in provider linked to a user."""


class AccountTable(SQLAlchemyBaseUserTableUUID, AdvancedDeclarativeBase):
    """A user who signs in with a password or a social provider."""

    # Loaded with the user, as fastapi-users reads them when a provider signs the user in. Declared without a
    # Mapped annotation, which SQLAlchemy accepts and ty cannot check against the relationship's Any
    oauth_accounts = relationship(OAuthAccountTable, lazy='joined')


class AccessTokenTable(SQLAlchemyBaseAccessTokenTableUUID, AdvancedDeclarativeBase):
    """An opaque session token, stored so that signing out revokes it."""


class AccountDatabase(SQLAlchemyUserDatabase[AccountTable, UUID]):
    """Users and their linked provider accounts in the request's session."""

    def __init__(self, session: AsyncSession) -> None:
        """Read and write users through ``session``.

        :param session: Session of the current request.
        :type session: AsyncSession
        """
        super().__init__(session, AccountTable, OAuthAccountTable)


class AccessTokenDatabase(SQLAlchemyAccessTokenDatabase[AccessTokenTable]):
    """Session tokens in the request's session."""

    def __init__(self, session: AsyncSession) -> None:
        """Read and write session tokens through ``session``.

        :param session: Session of the current request.
        :type session: AsyncSession
        """
        super().__init__(session, AccessTokenTable)
