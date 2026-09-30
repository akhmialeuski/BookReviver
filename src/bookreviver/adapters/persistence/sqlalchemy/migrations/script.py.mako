"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""

import warnings
from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}
## The JSONB variant of advanced-alchemy's JsonB renders as JSONB(astext_type=Text())
% if 'Text()' in (upgrades or '') + (downgrades or ''):
from sqlalchemy import Text
% endif

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('data_downgrades', 'data_upgrades', 'downgrade', 'schema_downgrades', 'schema_upgrades', 'upgrade')

# Revision identifiers, used by Alembic
revision: str = ${repr(up_revision)}
down_revision: str | Sequence[str] | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    """Change the schema, then the data, outside a transaction so that each statement commits on its own."""
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', category=UserWarning)
        with op.get_context().autocommit_block():
            schema_upgrades()
            data_upgrades()


def downgrade() -> None:
    """Revert the data, then the schema, outside a transaction so that each statement commits on its own."""
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', category=UserWarning)
        with op.get_context().autocommit_block():
            data_downgrades()
            schema_downgrades()


def schema_upgrades() -> None:
    """Change the schema to this revision."""
    ${upgrades if upgrades else ""}


def schema_downgrades() -> None:
    """Change the schema back to the previous revision."""
    ${downgrades if downgrades else ""}


def data_upgrades() -> None:
    """Change the rows the schema change affects, after it."""


def data_downgrades() -> None:
    """Change the rows back, before the schema change is reverted."""
