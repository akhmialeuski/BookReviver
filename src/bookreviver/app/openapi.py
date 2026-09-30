"""The ``bookreviver-openapi`` command: write the OpenAPI schema of the application to ``docs/openapi.json``.

The frontend client is generated from the committed schema, and a test compares the file with the schema the
application publishes, so a route that changes without the file failing the test cannot happen. The command builds
the application the way the tests do, with a placeholder signing secret and no social sign-in provider, because a
provider's routes exist only where its client is configured, and the committed schema describes the application with
none. It writes the file in the layout the ``pretty-format-json`` hook of the gate expects, so the hook leaves it as
it is.
"""

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import SecretStr

from bookreviver.app.main import create_app
from bookreviver.app.settings import AuthSettings, Settings

if TYPE_CHECKING:
    from collections.abc import Sequence

# The file the schema is committed to, relative to the root of the repository
SCHEMA_PATH: Path = Path('docs') / 'openapi.json'
# Signs nothing, since the application is only built to describe its routes and never serves a request
PLACEHOLDER_SECRET: str = 'schema-only-secret'
JSON_INDENT: int = 2


def openapi_schema() -> dict[str, Any]:
    """Return the OpenAPI schema of the application with no social sign-in provider.

    :returns: The schema as JSON-compatible data, the way the committed file holds it.
    :rtype: dict[str, Any]
    """
    app = create_app(Settings(auth=AuthSettings(secret=SecretStr(PLACEHOLDER_SECRET))))
    return json.loads(json.dumps(app.openapi()))


def main(argv: Sequence[str] | None = None) -> None:
    """Write the OpenAPI schema to a file, ``docs/openapi.json`` unless another path is given.

    :param argv: Command line arguments, or None for those of the process.
    :type argv: Sequence[str] | None
    """
    parser = argparse.ArgumentParser(prog='bookreviver-openapi', description=main.__doc__)
    parser.add_argument('path', nargs='?', type=Path, default=SCHEMA_PATH, help='file to write the schema to')
    path = Path(parser.parse_args(argv).path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'{json.dumps(openapi_schema(), indent=JSON_INDENT)}\n', encoding='utf-8')
