"""Fixtures of the import and job API tests: the application runs on fakes of the storage, imaging and event adapters."""

from typing import TYPE_CHECKING

import pytest

from tests.helpers.fakes_imports import ImportFakes, ImportFakesProvider

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from dishka import Provider


@pytest.fixture
def fx_import_fakes(tmp_path: Path) -> ImportFakes:
    """Build the fakes the application of an API test runs on, with their files under the test's directory."""
    return ImportFakes.under(tmp_path)


@pytest.fixture
def fx_extra_providers(fx_import_fakes: ImportFakes) -> Sequence[Provider]:
    """Replace the database, storage, imaging and event adapters with ``fx_import_fakes``."""
    return (ImportFakesProvider(fx_import_fakes),)
