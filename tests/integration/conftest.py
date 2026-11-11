"""Fixtures of the integration tests: the bus the changes of the books are recorded on, and the processing kit."""

from typing import TYPE_CHECKING

import pytest

from tests.harness.builders.processing import ProcessingKit

if TYPE_CHECKING:
    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from tests.harness.builders.domain import BookOpener
    from tests.harness.fakes.jobs import RecordingEventBus


@pytest.fixture
def fx_book_changes(fx_book_parts: RecordingEventBus) -> RecordingEventBus:
    """Return the bus the changes of the books that the test announces are recorded on.

    :param fx_book_parts: The bus registered for the test.
    :type fx_book_parts: RecordingEventBus
    :returns: The bus every book database of the test publishes to, which is the bus of the services too.
    :rtype: RecordingEventBus
    """
    return fx_book_parts


@pytest.fixture
def fx_kit(
    fx_database: SqlDatabase,
    fx_open_book: BookOpener,
) -> ProcessingKit:
    """Build what the processing services of a test share, over the database and the asset store of the test.

    :param fx_database: Database of the test.
    :type fx_database: SqlDatabase
    :param fx_open_book: Opens the books of the test with their databases.
    :type fx_open_book: BookOpener
    :returns: The kit that builds the processing service, the jobs of the workers and the edit service.
    :rtype: ProcessingKit
    """
    return ProcessingKit(fx_database, fx_open_book)
