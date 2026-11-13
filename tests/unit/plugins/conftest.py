"""Fixtures of the tests of the built-in plugins."""

import pytest

from bookreviver.app.plugins import EntryPointCatalog
from bookreviver.domain.processors import WorkerPool
from tests.harness.images.samples import CV_MISSING


@pytest.fixture(scope='module')
def fx_processors() -> EntryPointCatalog:
    """Load the built-in processors once for each module, which need OpenCV for the most part.

    :returns: The catalogue of the processors the project registers.
    :rtype: EntryPointCatalog
    """
    pytest.importorskip('cv2', reason=CV_MISSING)
    return EntryPointCatalog(pools=set(WorkerPool))
