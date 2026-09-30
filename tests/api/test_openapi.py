"""Tests for the OpenAPI schema the application publishes, across every router."""

import json
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.app.openapi import SCHEMA_PATH, main

if TYPE_CHECKING:
    from fastapi import FastAPI

pytestmark = pytest.mark.anyio

# Start of the reST parameter fields that route docstrings carry after their form feed
DOCSTRING_FIELD: str = ':param'
DESCRIPTION_KEY: str = 'description'
# The committed schema, found from the repository root as the command writes it
SCHEMA_FILE: Path = Path(__file__).parents[2] / SCHEMA_PATH


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


class TestCommittedSchema:
    """Tests for docs/openapi.json, the schema the frontend client is generated from."""

    async def test_file_equals_the_schema_the_application_publishes(self, fx_app: FastAPI) -> None:
        """Verify the committed file is what ``app.openapi()`` returns, so a route cannot change without it.

        Run ``uv run bookreviver-openapi`` to write the file again after a route or a schema changes.

        :param fx_app: The running application with every router included.
        :type fx_app: FastAPI
        """
        committed = json.loads(await anyio.Path(SCHEMA_FILE).read_text(encoding='utf-8'))

        assert committed == json.loads(json.dumps(fx_app.openapi()))

    async def test_command_writes_the_published_schema_in_the_layout_of_the_file(
        self, fx_app: FastAPI, tmp_path: Path
    ) -> None:
        """Verify the command writes the schema of the application and the committed file's exact text.

        :param fx_app: The running application with every router included.
        :type fx_app: FastAPI
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        target = tmp_path / 'nested' / 'openapi.json'

        main([str(target)])

        written = await anyio.Path(target).read_text(encoding='utf-8')
        expect(json.loads(written) == json.loads(json.dumps(fx_app.openapi())))
        expect(written == await anyio.Path(SCHEMA_FILE).read_text(encoding='utf-8'))
        assert_expectations()
