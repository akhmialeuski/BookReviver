"""Mappers between table rows and frozen domain entities, one per aggregate.

A domain entity and a table row have different shapes, and neither can stand in for the other. The entity is a frozen
``attrs`` class with nested value objects: a :class:`~bookreviver.domain.books.BookRecord` holds a
:class:`~bookreviver.domain.books.BookDetails`, and a :class:`~bookreviver.domain.versions.PageVersion` a
:class:`~bookreviver.domain.versions.ProcessorRef` and a :class:`~bookreviver.domain.geometry.Transform`. The row is a
flat, mutable class instrumented by SQLAlchemy, which assigns its attributes on load and tracks their changes. The
domain may not import SQLAlchemy, which import-linter enforces, so the translation lives here: ``to_row`` spreads the
value objects over columns, or into JSON where no query looks inside them, and ``to_entity`` gathers them back.

This is the Data Mapper pattern with explicit code. Imperative mapping of the domain classes is ruled out because the
ORM cannot assign attributes of a frozen class, and ``composite()`` is ruled out because it needs positional
constructors and a ``__composite_values__`` method on domain classes, which are keyword-only and must stay free of
storage concerns.
"""

from bookreviver.adapters.persistence.sqlalchemy.mappers.base import RowMapper
from bookreviver.adapters.persistence.sqlalchemy.mappers.book import (
    BookPlaceMapper,
    BookRecordMapper,
    contributors_from_json,
    contributors_to_json,
    identifiers_from_json,
    identifiers_to_json,
)
from bookreviver.adapters.persistence.sqlalchemy.mappers.pages import PageMapper, PaginationSectionMapper
from bookreviver.adapters.persistence.sqlalchemy.mappers.recipes import RecipeMapper
from bookreviver.adapters.persistence.sqlalchemy.mappers.sources import ScanMapper, SourceMapper
from bookreviver.adapters.persistence.sqlalchemy.mappers.stages import PageStageMapper
from bookreviver.adapters.persistence.sqlalchemy.mappers.step_state import (
    GroupValuesMapper,
    PageHistoryMapper,
    PageStepStateMapper,
    StepValuesMapper,
)
from bookreviver.adapters.persistence.sqlalchemy.mappers.versions import PageVersionMapper

__all__ = [
    'BookPlaceMapper',
    'BookRecordMapper',
    'GroupValuesMapper',
    'PageHistoryMapper',
    'PageMapper',
    'PageStageMapper',
    'PageStepStateMapper',
    'PageVersionMapper',
    'PaginationSectionMapper',
    'RecipeMapper',
    'RowMapper',
    'ScanMapper',
    'SourceMapper',
    'StepValuesMapper',
    'contributors_from_json',
    'contributors_to_json',
    'identifiers_from_json',
    'identifiers_to_json',
]
