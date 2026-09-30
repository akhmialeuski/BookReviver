"""Tests for the OpenAPI schema the application publishes, across every router."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect

if TYPE_CHECKING:
    from fastapi import FastAPI

pytestmark = pytest.mark.anyio

# Start of the reST parameter fields that route docstrings carry after their form feed
DOCSTRING_FIELD: str = ':param'
DESCRIPTION_KEY: str = 'description'


class TestOperationDescriptions:
    """Tests for the descriptions FastAPI takes from route docstrings."""

    async def test_no_operation_publishes_docstring_fields(self, fx_app: FastAPI) -> None:
        """Verify every route ends its published description at a form feed, before the reST fields of its docstring.

        :param fx_app: The running application with every router included.
        :type fx_app: FastAPI
        """
        leaking = [
            f'{method.upper()} {path}'
            for path, item in fx_app.openapi()['paths'].items()
            for method, operation in item.items()
            if DOCSTRING_FIELD in operation.get(DESCRIPTION_KEY, '')
        ]

        expect(bool(fx_app.openapi()['paths']))
        expect(leaking == [])
        assert_expectations()
