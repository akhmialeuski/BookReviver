"""Fixtures of the tests of the built-in plugins."""

import pytest

from bookreviver.app.plugins import EntryPointCatalog
from bookreviver.domain.processors import WorkerPool


@pytest.fixture(scope='module')
def fx_processors() -> EntryPointCatalog:
    """Load the built-in processors once for each module.

    :returns: The catalogue of the processors the project registers.
    :rtype: EntryPointCatalog
    """
    return EntryPointCatalog(pools=set(WorkerPool))
