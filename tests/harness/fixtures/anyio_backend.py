"""The async backend every level of the suite runs its async tests on."""

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Run async tests on asyncio, the loop the server uses.

    :returns: Name of the anyio backend.
    :rtype: str
    """
    return 'asyncio'
