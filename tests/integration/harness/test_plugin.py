"""Tests for the plugin of the suite, run on small suites that pytester writes and runs in the same process.

The suites have no ``__init__.py``, so pytest imports their modules by base name, and every module gets a name of its
own. Their conftest loads the plugin the way the root conftest of the project does, and stands a fixture that returns
nothing in for ``fx_app``, which lives in the conftest of the component tests.
"""

from typing import TYPE_CHECKING, NamedTuple, override

import pytest
from delayed_assert import assert_expectations, expect

from tests.harness.plugin import Level

if TYPE_CHECKING:
    from collections.abc import Callable

SUITE_CONFTEST: str = """
import pytest

pytest_plugins = ('tests.harness.plugin',)


@pytest.fixture
def fx_app():
    return None
"""
PASSING_TEST: str = 'def test_passes():\n    pass\n'
# The module of the one test of a level, without its suffix, as pytester names the files it writes
LEVEL_MODULE: str = 'tests/{level}/test_{level}_level'
OUTSIDE_MODULE: str = 'tests/test_no_level'
TAKING_MODULE: str = 'tests/{level}/test_takes_{fixture}'
TAKING_TEST: str = 'def test_takes({fixture}):\n    pass\n'
# Keys of the outcomes pytest counts in the summary line of a run
PASSED: str = 'passed'
ERRORS: str = 'errors'
DESELECTED: str = 'deselected'
CASE_PARAM: str = 'case'


class FixtureCase(NamedTuple):
    """A test of a level that takes one fixture.

    :ivar level: Level of the directory that holds the test.
    :ivar fixture: Name of the fixture the test takes.
    """

    level: Level
    fixture: str

    @override
    def __str__(self) -> str:
        """Name the case by its level and its fixture, as the identifier of a parametrized test.

        :returns: The level and the fixture joined by a hyphen.
        :rtype: str
        """
        return f'{self.level}-{self.fixture}'


REFUSED_CASES: tuple[FixtureCase, ...] = (
    FixtureCase(Level.UNIT, 'tmp_path'),
    FixtureCase(Level.UNIT, 'fx_database'),
    FixtureCase(Level.UNIT, 'fx_app'),
    FixtureCase(Level.INTEGRATION, 'fx_app'),
)
ALLOWED_CASES: tuple[FixtureCase, ...] = (
    FixtureCase(Level.INTEGRATION, 'tmp_path'),
    FixtureCase(Level.CONTRACT, 'tmp_path'),
    FixtureCase(Level.COMPONENT, 'fx_app'),
)


@pytest.fixture
def fx_suite(pytester: pytest.Pytester) -> pytest.Pytester:
    """Write a suite that loads the plugin and holds one passing test in the directory of every level.

    :param pytester: Writer and runner of the suite, in a temporary directory of the test.
    :type pytester: pytest.Pytester
    :returns: The writer, ready to run the suite.
    :rtype: pytest.Pytester
    """
    pytester.makeconftest(SUITE_CONFTEST)
    pytester.makepyfile(**{LEVEL_MODULE.format(level=level): PASSING_TEST for level in Level})
    return pytester


@pytest.fixture
def fx_run_taking(pytester: pytest.Pytester) -> Callable[[FixtureCase], pytest.RunResult]:
    """Return a function that writes a test of a level taking one fixture into a suite loading the plugin, and runs it.

    :param pytester: Writer and runner of the suite, in a temporary directory of the test.
    :type pytester: pytest.Pytester
    :returns: The function, called with the level and the fixture of the test.
    :rtype: Callable[[FixtureCase], pytest.RunResult]
    """
    pytester.makeconftest(SUITE_CONFTEST)

    def run_taking(case: FixtureCase) -> pytest.RunResult:
        """Write the test of the case and run it alone.

        :param case: Level of the test and the fixture it takes.
        :type case: FixtureCase
        :returns: Result of the run.
        :rtype: pytest.RunResult
        """
        module = TAKING_MODULE.format(level=case.level, fixture=case.fixture)
        return pytester.runpytest(pytester.makepyfile(**{module: TAKING_TEST.format(fixture=case.fixture)}))

    return run_taking


class TestPytestCollectionModifyitems:
    """Tests for the marker of the level that the plugin gives every collected test."""

    @pytest.mark.parametrize('level', list(Level), ids=str)
    def test_marker_of_a_level_selects_the_tests_of_its_directory_alone(
        self, fx_suite: pytest.Pytester, level: Level
    ) -> None:
        """Verify ``-m`` with a level runs the one test of that level's directory and deselects the others.

        :param fx_suite: Suite with one test per level.
        :type fx_suite: pytest.Pytester
        :param level: Level whose marker selects the tests.
        :type level: Level
        """
        result = fx_suite.runpytest('-m', level, '-v')

        outcomes = result.parseoutcomes()
        expect(outcomes.get(PASSED) == 1)
        expect(outcomes.get(DESELECTED) == len(Level) - 1)
        expect(f'{LEVEL_MODULE.format(level=level)}.py::test_passes PASSED' in result.stdout.str())
        assert_expectations()

    def test_test_outside_the_level_directories_stops_the_run(self, fx_suite: pytest.Pytester) -> None:
        """Verify a test straight under ``tests`` stops the run with a usage error that names it.

        :param fx_suite: Suite with one test per level.
        :type fx_suite: pytest.Pytester
        """
        fx_suite.makepyfile(**{OUTSIDE_MODULE: PASSING_TEST})

        result = fx_suite.runpytest()

        expect(result.ret == pytest.ExitCode.USAGE_ERROR)
        expect(f'{OUTSIDE_MODULE}.py' in result.stderr.str())
        assert_expectations()


class TestPytestRuntestSetup:
    """Tests for the check that a test takes no fixture its level refuses."""

    @pytest.mark.parametrize(CASE_PARAM, REFUSED_CASES, ids=str)
    def test_fixture_the_level_refuses_fails_the_setup_naming_the_level_and_the_fixture(
        self, fx_run_taking: Callable[[FixtureCase], pytest.RunResult], case: FixtureCase
    ) -> None:
        """Verify the test errors at setup with a message that names its level and the refused fixture.

        :param fx_run_taking: Writes and runs a test of a level that takes one fixture.
        :type fx_run_taking: Callable[[FixtureCase], pytest.RunResult]
        :param case: Level of the test and the fixture it takes, which the level refuses.
        :type case: FixtureCase
        """
        result = fx_run_taking(case)

        output = result.stdout.str()
        expect(result.parseoutcomes().get(ERRORS) == 1)
        expect(f'tests/{case.level} {case.level.label}, but the test takes' in output)
        expect(case.fixture in output)
        assert_expectations()

    @pytest.mark.parametrize(CASE_PARAM, ALLOWED_CASES, ids=str)
    def test_fixture_the_level_allows_passes_the_setup(
        self, fx_run_taking: Callable[[FixtureCase], pytest.RunResult], case: FixtureCase
    ) -> None:
        """Verify the test passes when its level allows the fixture it takes.

        :param fx_run_taking: Writes and runs a test of a level that takes one fixture.
        :type fx_run_taking: Callable[[FixtureCase], pytest.RunResult]
        :param case: Level of the test and the fixture it takes, which the level allows.
        :type case: FixtureCase
        """
        assert fx_run_taking(case).parseoutcomes().get(PASSED) == 1
