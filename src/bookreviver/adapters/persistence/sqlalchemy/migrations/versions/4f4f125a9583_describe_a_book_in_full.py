"""Describe a book in full.

The description of a book gains the subtitle, the parallel and original titles, the contributors with their roles, the
printer, the censor's permit, the number in the series, the languages, the script, the physical description, the
identifiers, the subjects, the rights and the marks of the scanned copy. The lists are JSON columns and the other
new columns are text or short enums with an empty default, so every existing row stays valid.

The single ``authors`` string becomes one contributor with the role ``aut``, because it did not tell people or roles
apart. A ``language`` that looks like a three-letter lower-case code becomes the only item of ``languages``, and any
other value is appended to ``notes`` as a line ``Language: ...`` so that it is not lost. The two old columns are then
dropped, and the downgrade rebuilds them from the first author and the first language.

Revision ID: 4f4f125a9583
Revises: 6446f5ce697c
Create Date: 2026-09-30 16:38:42.770696
"""

import re
import warnings
from typing import TYPE_CHECKING, Any

import advanced_alchemy.types.guid
import advanced_alchemy.types.json
import sqlalchemy as sa
from alembic import op
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('data_downgrades', 'data_upgrades', 'downgrade', 'schema_downgrades', 'schema_upgrades', 'upgrade')

# Revision identifiers, used by Alembic
revision: str = '4f4f125a9583'
down_revision: str | Sequence[str] | None = '6446f5ce697c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The type of the list columns, spelled as the tables spell it so PostgreSQL binds a value as JSONB
JSON_LIST = (
    sa.JSON()
    .with_variant(postgresql.JSONB(astext_type=Text()), 'cockroachdb')
    .with_variant(advanced_alchemy.types.json.ORA_JSONB(), 'oracle')
    .with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql')
)
# What the old ``language`` column had to look like to become a language code
LANGUAGE_CODE = re.compile(r'[a-z]{3}')
AUTHOR_ROLE = 'aut'
PROJECTS = sa.table(
    'projects',
    sa.column('id', advanced_alchemy.types.guid.GUID(length=16)),
    sa.column('authors', sa.String()),
    sa.column('language', sa.String()),
    sa.column('notes', sa.String()),
    sa.column('contributors', JSON_LIST),
    sa.column('languages', JSON_LIST),
)


def upgrade() -> None:
    """Add the columns, move the data into them, then drop the columns they replace, in one transaction."""
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', category=UserWarning)
        schema_upgrades()
        data_upgrades()
        with op.batch_alter_table('projects', schema=None) as batch_op:
            batch_op.drop_column('authors')
            batch_op.drop_column('language')


def downgrade() -> None:
    """Rebuild the replaced columns, fill them from the new ones, then drop the new columns, in one transaction.

    The rebuilt columns keep an empty default, which the previous revision did not declare but no row depends on.
    """
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', category=UserWarning)
        with op.batch_alter_table('projects', schema=None) as batch_op:
            batch_op.add_column(sa.Column('authors', sa.String(), server_default='', nullable=False))
            batch_op.add_column(sa.Column('language', sa.String(), server_default='', nullable=False))
        data_downgrades()
        schema_downgrades()


def schema_upgrades() -> None:
    """Add the columns of the extended description."""
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.add_column(sa.Column('subtitle', sa.String(), server_default='', nullable=False))
        batch_op.add_column(sa.Column('parallel_titles', JSON_LIST, server_default='[]', nullable=False))
        batch_op.add_column(sa.Column('original_title', sa.String(), server_default='', nullable=False))
        batch_op.add_column(sa.Column('contributors', JSON_LIST, server_default='[]', nullable=False))
        batch_op.add_column(sa.Column('printer', sa.String(), server_default='', nullable=False))
        batch_op.add_column(sa.Column('censorship', sa.String(), server_default='', nullable=False))
        batch_op.add_column(sa.Column('series_number', sa.String(), server_default='', nullable=False))
        batch_op.add_column(sa.Column('languages', JSON_LIST, server_default='[]', nullable=False))
        batch_op.add_column(
            sa.Column(
                'script',
                sa.Enum('unknown', 'cyrillic', 'latin', 'mixed', name='script', native_enum=False),
                server_default='unknown',
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column('printed_pagination', sa.String(), server_default='', nullable=False))
        batch_op.add_column(sa.Column('height_cm', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('illustrations', sa.String(), server_default='', nullable=False))
        batch_op.add_column(sa.Column('binding', sa.String(), server_default='', nullable=False))
        batch_op.add_column(sa.Column('identifiers', JSON_LIST, server_default='[]', nullable=False))
        batch_op.add_column(sa.Column('subjects', JSON_LIST, server_default='[]', nullable=False))
        batch_op.add_column(
            sa.Column(
                'rights',
                sa.Enum('unknown', 'public-domain', 'in-copyright', name='rightsstatus', native_enum=False),
                server_default='unknown',
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column('copy_holder', sa.String(), server_default='', nullable=False))
        batch_op.add_column(sa.Column('copy_notes', sa.String(), server_default='', nullable=False))


def schema_downgrades() -> None:
    """Drop the columns of the extended description."""
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.drop_column('copy_notes')
        batch_op.drop_column('copy_holder')
        batch_op.drop_column('rights')
        batch_op.drop_column('subjects')
        batch_op.drop_column('identifiers')
        batch_op.drop_column('binding')
        batch_op.drop_column('illustrations')
        batch_op.drop_column('height_cm')
        batch_op.drop_column('printed_pagination')
        batch_op.drop_column('script')
        batch_op.drop_column('languages')
        batch_op.drop_column('series_number')
        batch_op.drop_column('censorship')
        batch_op.drop_column('printer')
        batch_op.drop_column('contributors')
        batch_op.drop_column('original_title')
        batch_op.drop_column('parallel_titles')
        batch_op.drop_column('subtitle')


def data_upgrades() -> None:
    """Move ``authors`` into one contributor and ``language`` into the languages or the notes of each project."""
    bind = op.get_bind()
    for row in bind.execute(sa.select(PROJECTS.c.id, PROJECTS.c.authors, PROJECTS.c.language, PROJECTS.c.notes)).all():
        values: dict[str, Any] = {}
        if row.authors:
            values['contributors'] = [{'name': row.authors, 'role': AUTHOR_ROLE}]
        if LANGUAGE_CODE.fullmatch(row.language):
            values['languages'] = [row.language]
        elif row.language:
            values['notes'] = '\n'.join(part for part in (row.notes, f'Language: {row.language}') if part)
        if values:
            bind.execute(PROJECTS.update().where(PROJECTS.c.id == row.id).values(**values))


def data_downgrades() -> None:
    """Move the first author and the first language of each project back into their single columns."""
    bind = op.get_bind()
    for row in bind.execute(sa.select(PROJECTS.c.id, PROJECTS.c.contributors, PROJECTS.c.languages)).all():
        authors = [person['name'] for person in row.contributors if person['role'] == AUTHOR_ROLE]
        first_person = authors[0] if authors else next((person['name'] for person in row.contributors), '')
        first_language = row.languages[0] if row.languages else ''
        if first_person or first_language:
            bind.execute(
                PROJECTS.update().where(PROJECTS.c.id == row.id).values(authors=first_person, language=first_language)
            )
