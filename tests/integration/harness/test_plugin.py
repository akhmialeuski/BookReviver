"""Tests for the plugin of the suite, run on small suites that pytester writes and runs in the same process.

The suites have no ``__init__.py``, so pytest imports their modules by base name, and every module gets a name of its
own. Their conftest loads the plugin the way the root conftest of the project does.
"""

import pytest
from delayed_assert import assert_expectations, expect

from tests.harness.plugin import Level

PLUGIN_CONFTEST: str = "pytest_plugins = ('tests.harness.plugin',)"
PASSING_TEST: str = 'def test_passes():\n    pass\n'
# The module of the one test of a level, without its suffix, as pytester names the files it writes
LEVEL_MODULE: str = 'tests/{level}/test_{level}_level'
OUTSIDE_MODULE: str = 'tests/test_no_level'


@pytest.fixture
def fx_suite(pytester: pytest.Pytester) -> pytest.Pytester:
    """Write a suite that loads the plugin and holds one passing test in the directory of every level.

    :param pytester: Writer and runner of the suite, in a temporary directory of the test.
    :type pytester: pytest.Pytester
    :returns: The writer, ready to run the suite.
    :rtype: pytest.Pytester
    """
    pytester.makeconftest(PLUGIN_CONFTEST)
    pytester.makepyfile(**{LEVEL_MODULE.format(level=level): PASSING_TEST for level in Level})
    return pytester


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
        expect(outcomes.get('passed') == 1)
        expect(outcomes.get('deselected') == len(Level) - 1)
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
