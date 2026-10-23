"""The Alembic environment of the file of a book, which runs the shared environment with its schema."""

from bookreviver.adapters.persistence.sqlalchemy.database import Schema
from bookreviver.adapters.persistence.sqlalchemy.migrations.environment import run

run(Schema.BOOK)
