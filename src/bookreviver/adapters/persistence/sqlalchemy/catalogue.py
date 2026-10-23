"""Tables of the catalogue, the one database of the application beside the files of the books.

The catalogue holds what belongs to the accounts: the account tables of fastapi-users in
:mod:`bookreviver.adapters.persistence.sqlalchemy.accounts`, the ``library`` that says which account owns which book,
and the ``recipe_profiles`` of the accounts. The tables share advanced-alchemy's default metadata, apart from the
metadata of the tables of a book, and are declared in the same style as those.

The library keeps the rule that an account is deleted only after its books: its key to ``user`` carries
``ON DELETE RESTRICT``, because a cascade would remove the entries of the account's books but not the books, whose
files only ``ProjectService`` removes.
"""

from datetime import datetime
from typing import Any, Final
from uuid import UUID

from advanced_alchemy.base import DefaultBase
from sqlalchemy import JSON, ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from bookreviver.adapters.persistence.sqlalchemy.accounts import AccountTable
from bookreviver.adapters.persistence.sqlalchemy.columns import CASCADE, RESTRICT, enum_by_value
from bookreviver.domain.enums import OrderMode, Stage

LIBRARY_TABLE: Final = 'library'
# The column of the library that names the book, its key
BOOK_ID_COLUMN: Final = 'project_id'
RECIPE_PROFILES_TABLE: Final = 'recipe_profiles'
# The rows of the partial unique index of ``recipe_profiles``: the default profile of a stage of an account
DEFAULT_PROFILE: Final = text('is_default')


class LibraryRow(DefaultBase):
    """Row of one book in the library of the account that owns it.

    :ivar book_id: The book, whose file lives in the directory of the book and is reached through no key.
    :ivar owner_id: Account owning the book, which is not deleted while it owns a book.
    :ivar added_at: Time the book joined the library, which orders the owner's list of books.
    """

    __tablename__ = LIBRARY_TABLE

    book_id: Mapped[UUID] = mapped_column(BOOK_ID_COLUMN, primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey(AccountTable.__table__.c.id, ondelete=RESTRICT), index=True)
    added_at: Mapped[datetime]


class RecipeProfileRow(DefaultBase):
    """Row of one recipe profile of an account, of which at most one per stage is the default.

    The steps are a JSON list of ``{processor_key, params, enabled, step_id}`` objects, as in a recipe. No column of the
    profile refers to a book. A recipe of a book may name the profile it was made from, which lives in another file, so
    the service that deletes a profile empties that name in the owner's books, and a profile is removed only with its
    account or by its owner.

    :ivar id: Profile identifier, assigned by the domain.
    :ivar account_id: Account owning the profile, whose deletion removes the profile.
    :ivar stage: Stage whose recipes the profile can be applied to, stored by value.
    :ivar name: Name the user sees.
    :ivar steps: The steps in order, as JSON.
    :ivar order: Whether the steps were saved in the usual order or the free one, stored by value.
    :ivar is_default: Whether a new book of the account starts the stage with this profile.
    :ivar created_at: Time the profile was saved.
    :ivar updated_at: Time the profile last changed.
    """

    __tablename__ = RECIPE_PROFILES_TABLE
    # The database keeps two requests that both choose the default profile of a stage from both succeeding
    __table_args__ = (
        Index('ix_recipe_profiles_one_default', 'account_id', 'stage', unique=True, sqlite_where=DEFAULT_PROFILE),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    account_id: Mapped[UUID] = mapped_column(ForeignKey(AccountTable.__table__.c.id, ondelete=CASCADE), index=True)
    stage: Mapped[Stage] = mapped_column(enum_by_value(Stage))
    name: Mapped[str]
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    order: Mapped[OrderMode] = mapped_column(enum_by_value(OrderMode), server_default=OrderMode.USUAL.value)
    is_default: Mapped[bool]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
