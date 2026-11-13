"""Tables of a book, private to the SQLAlchemy persistence adapter, which every book keeps in a file of its own.

The package declares the ``book_records``, ``sources``, ``scans``, ``pages``, ``page_versions``, ``page_stages``,
``page_step_states``, ``step_changes``, ``page_history``, ``page_groups``, ``step_values``, ``group_values``,
``pagination_sections``, ``book_places``, ``recipes`` and ``recipe_profiles`` tables in the SQLAlchemy 2.0 declarative
style: ``Mapped`` annotations, ``mapped_column`` and ``relationship`` with ``back_populates``. Every table derives from
:class:`BookTable`, which carries the bind key ``book``, so advanced-alchemy gives the tables of a book a ``MetaData``
of their own, apart from the tables of the catalogue in :mod:`bookreviver.adapters.persistence.sqlalchemy.catalogue`,
and the migrations of the two schemas never meet. The base also brings the portable ``GUID`` and ``DateTimeUTC`` column
types for ``UUID`` and ``datetime`` annotations and the naming convention of keys and constraints. The tables are
written for SQLite alone, the one database of the application, so no column or index carries a variant for another
database.

Rows never leave the adapter. The mappers in :mod:`bookreviver.adapters.persistence.sqlalchemy.mappers` turn them into
frozen domain entities, so nothing outside this package depends on the shape of a table.

The file of a book holds the record of the book. The book owns its sources, scans, pages, recipes and recipe profiles,
a source owns its scans, and a page owns its versions, its stage records, its step states and its history. The
foreign keys carry ``ON DELETE CASCADE``, so the database removes them with their owner. An optional reference carries
``ON DELETE SET NULL`` instead: a page keeps its row when its scan is deleted, a version when its input version is,
and a stage record when its head version or its recipe is, because a page of the book holds its own copy of its
image. The relationships use ``passive_deletes=True`` to leave that deletion to the database, and ``lazy="raise"`` so
that no query runs unseen on attribute access, inside a transaction or after its session has closed. Unique keys are
declared with their table, and the shared naming convention names them.

No table of a book names an account. The owner of the book is a row of the library in the catalogue. A recipe names the
profile it was made from by its identifier alone, and the service that deletes the profile empties the name.
"""

from bookreviver.adapters.persistence.sqlalchemy.tables.base import (
    BOOK_BIND_KEY,
    BOOK_PLACE_ID,
    BOOK_RECORD_ID,
    BOOK_RECORDS_TABLE,
    CHANGE_ID_COLUMN,
    CHILD_CASCADE,
    EMPTY_LIST,
    EMPTY_OBJECT,
    EMPTY_TEXT,
    FIRST_VERSION,
    GROUP_ID_COLUMN,
    GROUP_LABEL_GIVEN,
    GROUP_OF_A_GROUP,
    ID_COLUMN,
    IMPORTED_SOURCE,
    LEGACY_FULL_FORMAT,
    MANUAL_LABEL_GIVEN,
    METADATA_COLUMN,
    NO_IMPLICIT_LOAD,
    PAGE_ID_COLUMN,
    PAGE_VERSIONS_TABLE,
    PAGES_TABLE,
    PAGINATION_SECTIONS_TABLE,
    PARITY_SCOPE,
    RECIPE_PROFILES_TABLE,
    RECIPES_TABLE,
    SEQUENCE_COLUMN,
    STAGE_COLUMN,
    VERSION_ID_LENGTH,
    BookTable,
    Relation,
)
from bookreviver.adapters.persistence.sqlalchemy.tables.book import BookPlaceRow, BookRecordRow
from bookreviver.adapters.persistence.sqlalchemy.tables.pages import (
    PageGroupRow,
    PageHistoryRow,
    PageRow,
    PageStageRow,
    PageStepStateRow,
    PageVersionRow,
    PaginationSectionRow,
    StepChangeRow,
)
from bookreviver.adapters.persistence.sqlalchemy.tables.recipes import RecipeProfileRow, RecipeRow
from bookreviver.adapters.persistence.sqlalchemy.tables.sources import ScanRow, SourceRow
from bookreviver.adapters.persistence.sqlalchemy.tables.step_values import GroupValuesRow, StepValuesRow

__all__ = [
    'BOOK_BIND_KEY',
    'BOOK_PLACE_ID',
    'BOOK_RECORDS_TABLE',
    'BOOK_RECORD_ID',
    'CHANGE_ID_COLUMN',
    'CHILD_CASCADE',
    'EMPTY_LIST',
    'EMPTY_OBJECT',
    'EMPTY_TEXT',
    'FIRST_VERSION',
    'GROUP_ID_COLUMN',
    'GROUP_LABEL_GIVEN',
    'GROUP_OF_A_GROUP',
    'ID_COLUMN',
    'IMPORTED_SOURCE',
    'LEGACY_FULL_FORMAT',
    'MANUAL_LABEL_GIVEN',
    'METADATA_COLUMN',
    'NO_IMPLICIT_LOAD',
    'PAGES_TABLE',
    'PAGE_ID_COLUMN',
    'PAGE_VERSIONS_TABLE',
    'PAGINATION_SECTIONS_TABLE',
    'PARITY_SCOPE',
    'RECIPES_TABLE',
    'RECIPE_PROFILES_TABLE',
    'SEQUENCE_COLUMN',
    'STAGE_COLUMN',
    'VERSION_ID_LENGTH',
    'BookPlaceRow',
    'BookRecordRow',
    'BookTable',
    'GroupValuesRow',
    'PageGroupRow',
    'PageHistoryRow',
    'PageRow',
    'PageStageRow',
    'PageStepStateRow',
    'PageVersionRow',
    'PaginationSectionRow',
    'RecipeProfileRow',
    'RecipeRow',
    'Relation',
    'ScanRow',
    'SourceRow',
    'StepChangeRow',
    'StepValuesRow',
]
