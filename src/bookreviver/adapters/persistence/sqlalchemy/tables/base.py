"""The base of the tables of a book, and the names and constants its tables share."""

import enum
from typing import Final

from advanced_alchemy.base import DefaultBase
from sqlalchemy import text

from bookreviver.domain.images import Rendition
from bookreviver.domain.sources import SourceStatus
from bookreviver.domain.step_values import ValueScope

# Format of the ``full`` image of the scans stored before the format was recorded, which were all JPEG
LEGACY_FULL_FORMAT: Final = Rendition.FULL_JPEG.value


# The bind key of the tables of a book, under which advanced-alchemy keeps their metadata and the engines of a book
BOOK_BIND_KEY: Final = 'book'


# Name of the column holding a source's metadata, which the declarative base reserves as an attribute name
METADATA_COLUMN: Final = 'metadata'


# ORM cascade of a parent to its children; deletion itself is left to the database through CASCADE
CHILD_CASCADE: Final = 'all, delete'


# Loading strategy that raises instead of emitting hidden SQL on attribute access
NO_IMPLICIT_LOAD: Final = 'raise'


# Server defaults of the columns the description gained, which existing rows take when the columns are added
EMPTY_TEXT: Final = ''


EMPTY_LIST: Final = '[]'


BOOK_RECORDS_TABLE: Final = 'book_records'


PAGES_TABLE: Final = 'pages'


PAGE_VERSIONS_TABLE: Final = 'page_versions'


PAGINATION_SECTIONS_TABLE: Final = 'pagination_sections'


RECIPES_TABLE: Final = 'recipes'


RECIPE_PROFILES_TABLE: Final = 'recipe_profiles'


# Columns that several keys, indexes and constraints name
ID_COLUMN: Final = 'id'


PAGE_ID_COLUMN: Final = 'page_id'


STAGE_COLUMN: Final = 'stage'


SEQUENCE_COLUMN: Final = 'sequence'


# Length of a page version identifier, a hash cut to 16 hexadecimal digits
VERSION_ID_LENGTH: Final = 16


# Server default of a column that holds a JSON object
EMPTY_OBJECT: Final = '{}'


# The rows of the partial unique index of ``sources``: the imported sources, which are the ones that join the book
IMPORTED_SOURCE: Final = text(f"status = '{SourceStatus.IMPORTED}'")


# The version of a book no change has written yet
FIRST_VERSION: Final = text('0')


# The key of the one row of ``book_places``
BOOK_PLACE_ID: Final = 1


# The key of the one row of the record of a book, which names no book, so the file of a book is the same in any library
BOOK_RECORD_ID: Final = 1


# A manual label is a label, so the empty label is never one; null leaves the number to the sections
MANUAL_LABEL_GIVEN: Final = "manual_label <> ''"


# The label of a group names it, so the empty label is never one
GROUP_LABEL_GIVEN: Final = "label <> ''"


# A change of the values of a group names its group, and no other change names one
GROUP_OF_A_GROUP: Final = f"(scope = '{ValueScope.GROUP}') = (group_id IS NOT NULL)"


# The values of the odd and the even pages, which ``step_values`` holds apart from those of a group
PARITY_SCOPE: Final = f"scope IN ('{ValueScope.ODD}', '{ValueScope.EVEN}')"


# Column of the key of a group, which the rows of a group name
GROUP_ID_COLUMN: Final = 'group_id'


# Column of the change a row of the history of a page names
CHANGE_ID_COLUMN: Final = 'change_id'


class Relation(enum.StrEnum):
    """Attribute names of the relationships, which ``back_populates`` refers to by name."""

    SCANS = 'scans'
    VERSIONS = 'versions'
    STAGES = 'stages'
    STEP_STATES = 'step_states'
    HISTORY = 'history'
    SOURCE = 'source'
    PAGE = 'page'


class BookTable(DefaultBase):
    """The base of the tables of a book, whose bind key gives them the metadata of the schema of a book."""

    __abstract__ = True
    __bind_key__ = BOOK_BIND_KEY
