"""Check the values of a book and index the input of a version.

The key of a version to the version it read gets an index, since the collection of versions deletes by it, and every
column of an enum gets a ``CHECK`` of its values. The revision stops before it changes anything when a row holds a value
outside its enum.

Revision ID: 1060478e5d99
Revises: 99e96bc47c3c
Create Date: 2026-10-22 17:47:55.523149
"""

import warnings
from typing import TYPE_CHECKING

from alembic import op

from bookreviver.adapters.persistence.sqlalchemy.migrations.operations import (
    ValueCheck,
    add_value_checks,
    drop_value_checks,
    refuse_values_outside,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('data_downgrades', 'data_upgrades', 'downgrade', 'schema_downgrades', 'schema_upgrades', 'upgrade')

# Revision identifiers, used by Alembic
revision: str = '1060478e5d99'
down_revision: str | Sequence[str] | None = '99e96bc47c3c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHECKS: tuple[ValueCheck, ...] = (
    ValueCheck('projects', 'ck_projects_orthography', 'orthography', ('unknown', 'pre-reform', 'modern')),
    ValueCheck('projects', 'ck_projects_script', 'script', ('unknown', 'cyrillic', 'latin', 'mixed')),
    ValueCheck('projects', 'ck_projects_rightsstatus', 'rights', ('unknown', 'public-domain', 'in-copyright')),
    ValueCheck('projects', 'ck_projects_imagepolicy', 'image_policy', ('compact', 'lossless')),
    ValueCheck('book_places', 'ck_book_places_placemode', 'mode', ('workspace', 'reading')),
    ValueCheck(
        'book_places',
        'ck_book_places_stage',
        'stage',
        (
            'import',
            'page-split',
            'page-order',
            'geometry',
            'cleanup',
            'layout',
            'background',
            'recognition',
            'proofreading',
            'typesetting',
        ),
    ),
    ValueCheck('book_places', 'ck_book_places_viewmode', 'view', ('page', 'spread', 'grid')),
    ValueCheck('book_places', 'ck_book_places_comparemode', 'compare', ('off', 'swipe', 'side')),
    ValueCheck('book_places', 'ck_book_places_pagefilter', 'filter', ('all', 'check', 'bad', 'left-out', 'wide')),
    ValueCheck(
        'recipes',
        'ck_recipes_stage',
        'stage',
        (
            'import',
            'page-split',
            'page-order',
            'geometry',
            'cleanup',
            'layout',
            'background',
            'recognition',
            'proofreading',
            'typesetting',
        ),
    ),
    ValueCheck('recipes', 'ck_recipes_recipekind', 'kind', ('text', 'color-picture', 'bw-picture', 'blank')),
    ValueCheck('sources', 'ck_sources_sourcekind', 'kind', ('pdf', 'djvu', 'image')),
    ValueCheck('sources', 'ck_sources_filetype', 'file_type', ('pdf', 'djvu', 'tiff', 'jpeg', 'jpeg-2000', 'png')),
    ValueCheck('sources', 'ck_sources_sourcestatus', 'status', ('imported', 'rejected', 'skipped')),
    ValueCheck(
        'sources',
        'ck_sources_rejectionreason',
        'rejection',
        ('duplicate', 'unreadable', 'unsupported-type', 'system-file'),
    ),
    ValueCheck('step_values', 'ck_step_values_valuescope', 'scope', ('pages', 'group', 'odd', 'even')),
    ValueCheck(
        'step_values',
        'ck_step_values_stage',
        'stage',
        (
            'import',
            'page-split',
            'page-order',
            'geometry',
            'cleanup',
            'layout',
            'background',
            'recognition',
            'proofreading',
            'typesetting',
        ),
    ),
    ValueCheck('scans', 'ck_scans_colormode', 'color_mode', ('bilevel', 'gray', 'color', 'unknown')),
    ValueCheck(
        'scans',
        'ck_scans_rendition',
        'renditions_full',
        ('full.jpg', 'full.png', 'preview.jpg', 'thumb.jpg', 'iiif', 'mask.png', 'mesh.json'),
    ),
    ValueCheck(
        'pages',
        'ck_pages_pagekind',
        'kind',
        ('cover', 'back-cover', 'endpaper', 'frontispiece', 'title', 'text', 'plate', 'blank', 'other'),
    ),
    ValueCheck('pages', 'ck_pages_contenttype', 'content_type', ('text', 'color-picture', 'bw-picture')),
    ValueCheck('pages', 'ck_pages_pageorigin', 'origin', ('scan', 'blank', 'placeholder')),
    ValueCheck('pages', 'ck_pages_blankfill', 'blank_fill', ('scan', 'white', 'paper')),
    ValueCheck(
        'page_step_changes',
        'ck_page_step_changes_stage',
        'stage',
        (
            'import',
            'page-split',
            'page-order',
            'geometry',
            'cleanup',
            'layout',
            'background',
            'recognition',
            'proofreading',
            'typesetting',
        ),
    ),
    ValueCheck('page_step_changes', 'ck_page_step_changes_steplayer', 'layer', ('settings', 'found', 'hand')),
    ValueCheck('page_step_changes', 'ck_page_step_changes_valuescope', 'scope', ('pages', 'group', 'odd', 'even')),
    ValueCheck(
        'page_step_changes', 'ck_page_step_changes_changesource', 'source', ('user', 'run', 'carry-over', 'undo')
    ),
    ValueCheck(
        'page_step_states',
        'ck_page_step_states_stage',
        'stage',
        (
            'import',
            'page-split',
            'page-order',
            'geometry',
            'cleanup',
            'layout',
            'background',
            'recognition',
            'proofreading',
            'typesetting',
        ),
    ),
    ValueCheck(
        'page_step_states',
        'ck_page_step_states_editorkind',
        'kind',
        ('none', 'rect', 'quad', 'line', 'rotation', 'split', 'mesh', 'brush-mask', 'regions', 'content-box'),
    ),
    ValueCheck(
        'page_versions',
        'ck_page_versions_stage',
        'stage',
        (
            'import',
            'page-split',
            'page-order',
            'geometry',
            'cleanup',
            'layout',
            'background',
            'recognition',
            'proofreading',
            'typesetting',
        ),
    ),
    ValueCheck(
        'page_versions',
        'ck_page_versions_reviewreason',
        'review',
        (
            'low-confidence',
            'not-applied',
            'unsure-gutter',
            'narrow-gutter',
            'cut-by-edge',
            'size-differs',
            'few-lines',
            'high-residual',
        ),
    ),
    ValueCheck(
        'page_versions',
        'ck_page_versions_rendition',
        'renditions_full',
        ('full.jpg', 'full.png', 'preview.jpg', 'thumb.jpg', 'iiif', 'mask.png', 'mesh.json'),
    ),
    ValueCheck('page_versions', 'ck_page_versions_versionstate', 'state', ('pending', 'running', 'ready', 'failed')),
    ValueCheck('page_versions', 'ck_page_versions_versionscale', 'scale', ('full', 'preview')),
    ValueCheck('page_versions', 'ck_page_versions_resultmark', 'mark', ('good', 'bad')),
    ValueCheck(
        'pagination_sections',
        'ck_pagination_sections_labelstyle',
        'style',
        ('arabic', 'roman-lower', 'roman-upper', 'alpha-lower', 'alpha-upper', 'none'),
    ),
    ValueCheck(
        'pagination_sections', 'ck_pagination_sections_numberdisplay', 'display', ('not-counted', 'counted', 'printed')
    ),
    ValueCheck(
        'page_stages',
        'ck_page_stages_stage',
        'stage',
        (
            'import',
            'page-split',
            'page-order',
            'geometry',
            'cleanup',
            'layout',
            'background',
            'recognition',
            'proofreading',
            'typesetting',
        ),
    ),
    ValueCheck('page_stages', 'ck_page_stages_stagestate', 'state', ('fresh', 'stale', 'failed')),
    ValueCheck('result_mark_changes', 'ck_result_mark_changes_mark_before', 'mark_before', ('good', 'bad')),
    ValueCheck('result_mark_changes', 'ck_result_mark_changes_mark_after', 'mark_after', ('good', 'bad')),
)


def upgrade() -> None:
    """Change the schema, then the data, in the transaction of the migration, which commits or rolls back whole."""
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', category=UserWarning)
        schema_upgrades()
        data_upgrades()


def downgrade() -> None:
    """Revert the data, then the schema, in the transaction of the migration, which commits or rolls back whole."""
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', category=UserWarning)
        data_downgrades()
        schema_downgrades()


def schema_upgrades() -> None:
    """Change the schema to this revision, after the check that it can.

    :raises RuntimeError: If a row holds a value outside its enum.
    """
    refuse_values_outside(CHECKS)
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('page_versions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_page_versions_input_id'), ['input_id'], unique=False)

    # ### end Alembic commands ###
    add_value_checks(CHECKS)


def schema_downgrades() -> None:
    """Change the schema back to the previous revision."""
    drop_value_checks(CHECKS)
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('page_versions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_page_versions_input_id'))

    # ### end Alembic commands ###


def data_upgrades() -> None:
    """Change the rows the schema change affects, after it."""


def data_downgrades() -> None:
    """Change the rows back, before the schema change is reverted."""
