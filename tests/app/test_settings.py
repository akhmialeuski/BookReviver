"""Tests for the settings group that switches the measurements of the backend on."""

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from bookreviver.app.settings import TRACING_NEEDS_PATH, TRACING_PATH_WITHOUT_TRACING, Settings, TracingSettings

if TYPE_CHECKING:
    from pathlib import Path

SPAN_FILE_NAME: str = 'spans.jsonl'
SECRET: str = 'test-secret-that-is-long-enough-for-signing'
ENABLED_VARIABLE: str = 'BOOKREVIVER_TRACING__ENABLED'
EXPORT_PATH_VARIABLE: str = 'BOOKREVIVER_TRACING__EXPORT_PATH'
SECRET_VARIABLE: str = 'BOOKREVIVER_AUTH__SECRET'
TRUE_VALUE: str = 'true'


class TestTracingSettings:
    """Tests for ``TracingSettings`` and the way the environment reaches it."""

    def test_tracing_is_off_by_default_and_names_no_file(self) -> None:
        """Verify an application started with no setting records nothing."""
        tracing = TracingSettings()
        assert (tracing.enabled, tracing.export_path, tracing.sink) == (False, None, None)

    def test_enabling_it_without_a_path_is_refused(self) -> None:
        """Verify the measurements cannot be switched on with nowhere to write them."""
        with pytest.raises(ValidationError, match=TRACING_NEEDS_PATH):
            TracingSettings(enabled=True)

    def test_a_path_with_the_measurements_off_is_refused(self, tmp_path: Path) -> None:
        """Verify a path left in the environment cannot suggest that the measurements are on.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        with pytest.raises(ValidationError, match=TRACING_PATH_WITHOUT_TRACING):
            TracingSettings(enabled=False, export_path=tmp_path / SPAN_FILE_NAME)

    def test_enabled_with_a_path_gives_the_file_as_its_sink(self, tmp_path: Path) -> None:
        """Verify the file the spans are written to is the path of the settings once the measurements are on.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path = tmp_path / SPAN_FILE_NAME
        assert TracingSettings(enabled=True, export_path=path).sink == path

    def test_environment_variables_switch_it_on_through_the_nested_delimiter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify the variables named for the end-to-end run reach the group, and are checked like its arguments.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param monkeypatch: Fixture that restores the environment after the test.
        :type monkeypatch: pytest.MonkeyPatch
        """
        path = tmp_path / SPAN_FILE_NAME
        monkeypatch.setenv(SECRET_VARIABLE, SECRET)
        monkeypatch.setenv(ENABLED_VARIABLE, TRUE_VALUE)
        monkeypatch.setenv(EXPORT_PATH_VARIABLE, str(path))
        settings = Settings(_env_file=None)
        assert settings.tracing.sink == path

    def test_environment_without_the_path_is_refused_as_a_whole(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify the settings of an application started with the switch and no file do not load.

        :param monkeypatch: Fixture that restores the environment after the test.
        :type monkeypatch: pytest.MonkeyPatch
        """
        monkeypatch.setenv(SECRET_VARIABLE, SECRET)
        monkeypatch.setenv(ENABLED_VARIABLE, TRUE_VALUE)
        with pytest.raises(ValidationError, match=TRACING_NEEDS_PATH):
            Settings(_env_file=None)
