"""Tests for the settings groups that switch the measurements of the backend on and size the pool of the steps."""

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from bookreviver.app.settings import (
    STEP_PROCESSES_NEED_PREVIEW_SHARE,
    TRACING_NEEDS_PATH,
    TRACING_PATH_WITHOUT_TRACING,
    ProcessingSettings,
    Settings,
    TracingSettings,
)

if TYPE_CHECKING:
    from pathlib import Path

SPAN_FILE_NAME: str = 'spans.jsonl'
SECRET: str = 'test-secret-that-is-long-enough-for-signing'
ENABLED_VARIABLE: str = 'BOOKREVIVER_TRACING__ENABLED'
EXPORT_PATH_VARIABLE: str = 'BOOKREVIVER_TRACING__EXPORT_PATH'
SECRET_VARIABLE: str = 'BOOKREVIVER_AUTH__SECRET'
TRUE_VALUE: str = 'true'
STEP_PROCESSES_VARIABLE: str = 'BOOKREVIVER_PROCESSING__STEP_PROCESSES'
PREVIEW_STEP_PROCESSES_VARIABLE: str = 'BOOKREVIVER_PROCESSING__PREVIEW_STEP_PROCESSES'
# Where the default number of processes reads the cores, which a test replaces
CPU_COUNT_TARGET: str = 'bookreviver.app.settings.os.cpu_count'


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


class TestProcessingSettings:
    """Tests for the processes the steps of processors run in."""

    @pytest.mark.parametrize(
        ('cores', 'expected'),
        [(None, 2), (1, 2), (2, 2), (4, 2), (6, 4), (12, 8)],
        ids=['unknown', 'one', 'two', 'four', 'six', 'twelve'],
    )
    def test_the_default_is_two_thirds_of_the_cores_and_at_least_two(
        self, monkeypatch: pytest.MonkeyPatch, cores: int | None, expected: int
    ) -> None:
        """Verify the number of processes follows the cores of the machine, rounded down, with two as the floor.

        :param monkeypatch: Fixture that restores ``os.cpu_count`` after the test.
        :type monkeypatch: pytest.MonkeyPatch
        :param cores: What ``os.cpu_count`` reports.
        :type cores: int | None
        :param expected: The number of processes the default names.
        :type expected: int
        """
        monkeypatch.setattr(CPU_COUNT_TARGET, lambda: cores)
        assert ProcessingSettings().step_processes == expected

    def test_one_process_is_kept_for_previews_by_default(self) -> None:
        """Verify previews have a process of their own unless the settings say more."""
        assert ProcessingSettings().preview_step_processes == 1

    @pytest.mark.parametrize(('steps', 'previews'), [(2, 2), (2, 3), (4, 4)], ids=['equal', 'more', 'all'])
    def test_previews_may_not_take_every_process(self, steps: int, previews: int) -> None:
        """Verify the settings are refused when no process is left for the full runs.

        :param steps: Processes of all the steps.
        :type steps: int
        :param previews: Processes of the previews.
        :type previews: int
        """
        with pytest.raises(ValidationError, match=STEP_PROCESSES_NEED_PREVIEW_SHARE):
            ProcessingSettings(step_processes=steps, preview_step_processes=previews)

    def test_previews_may_leave_the_rest_to_the_full_runs(self) -> None:
        """Verify the settings that leave one process to the full runs are accepted."""
        settings = ProcessingSettings(step_processes=3, preview_step_processes=2)
        assert (settings.step_processes, settings.preview_step_processes) == (3, 2)

    def test_environment_variables_reach_the_group_through_the_nested_delimiter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify the variables of the two settings are read, and checked like the arguments of the group.

        :param monkeypatch: Fixture that restores the environment after the test.
        :type monkeypatch: pytest.MonkeyPatch
        """
        monkeypatch.setenv(SECRET_VARIABLE, SECRET)
        monkeypatch.setenv(STEP_PROCESSES_VARIABLE, '5')
        monkeypatch.setenv(PREVIEW_STEP_PROCESSES_VARIABLE, '2')
        processing = Settings(_env_file=None).processing
        assert (processing.step_processes, processing.preview_step_processes) == (5, 2)
