"""Fixtures of the integration tests of the application: the context of an HTTP request in a dishka scope."""

from typing import TYPE_CHECKING

import pytest
from starlette.requests import Request

if TYPE_CHECKING:
    from typing import Any


@pytest.fixture
def fx_request_context() -> dict[Any, Any]:
    """Build the context dishka's FastAPI integration puts into the scope of every HTTP request.

    :returns: The context holding a ``Request``, which a scope opened with it takes the limit of a request from.
    :rtype: dict[Any, Any]
    """
    return {Request: Request({'type': 'http'})}
