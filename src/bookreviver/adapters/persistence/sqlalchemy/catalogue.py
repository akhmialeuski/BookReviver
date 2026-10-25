"""Tables of the catalogue, the one database of the application beside the files of the books.

The catalogue holds what belongs to the accounts: the account tables of fastapi-users in
:mod:`bookreviver.adapters.persistence.sqlalchemy.accounts`, and the ``library`` that says which account owns which
book. The tables share advanced-alchemy's default metadata, apart from the metadata of the tables of a book, and are
declared in the same style as those.

The library keeps the rule that an account is deleted only after its books: its key to ``user`` carries
``ON DELETE RESTRICT``, because a cascade would remove the entries of the account's books but not the books, whose
files only ``BooksService`` removes.
"""

from datetime import datetime
from typing import Final
from uuid import UUID

from advanced_alchemy.base import DefaultBase
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from bookreviver.adapters.persistence.sqlalchemy.accounts import AccountTable
from bookreviver.adapters.persistence.sqlalchemy.columns import RESTRICT

LIBRARY_TABLE: Final = 'library'
# The column of the library that names the book, its key
BOOK_ID_COLUMN: Final = 'project_id'


class LibraryRow(DefaultBase):
    """Row of one book in the library of the account that owns it.

    :ivar book_id: The book, whose file lives in the directory of the book and is reached through no key.
    :ivar owner_id: Account owning the book, which is not deleted while it owns a book.
    :ivar directory: Name of the directory of the book in the storage, which no other book shares.
    :ivar added_at: Time the book joined the library, which orders the owner's list of books.
    """

    __tablename__ = LIBRARY_TABLE

    book_id: Mapped[UUID] = mapped_column(BOOK_ID_COLUMN, primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey(AccountTable.__table__.c.id, ondelete=RESTRICT), index=True)
    directory: Mapped[str] = mapped_column(unique=True)
    added_at: Mapped[datetime]
