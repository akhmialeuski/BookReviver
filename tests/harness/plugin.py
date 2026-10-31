"""The pytest plugin of the suite, which marks every test with the level of its directory and holds it to that level.

The levels are the directories right under ``tests``, so ``-m unit`` selects the tests of ``tests/unit`` and nothing
else. A test anywhere else has no level, so its collection stops the run instead of leaving it unmarked. A test that
takes a fixture its level refuses, such as a unit test taking ``tmp_path``, fails at setup before any fixture runs.
The plugin sees only fixtures, so a test that opens a file or starts a process by itself is left to review. It also
loads the fixtures that tests of several levels share, and pytester for its own tests.
"""

from typing import TYPE_CHECKING

import pytest

from bookreviver.domain.labels import LabeledStrEnum

if TYPE_CHECKING:
    from collections.abc import Mapping

pytest_plugins = (
    'pytester',
    'tests.harness.fixtures.anyio_backend',
    'tests.harness.fixtures.settings',
    'tests.harness.fixtures.databases',
)

# The directory under the root of the project that holds one directory per level
TESTS_DIR: str = 'tests'
# What joins the names a message lists
NAME_SEPARATOR: str = ', '


class Level(LabeledStrEnum):
    """Level of a test, which is the name of the directory under ``tests`` that holds it."""

    UNIT = 'unit', 'runs in memory, with no file, process, clock or database'
    INTEGRATION = (
        'integration',
        'uses one real technology in the process, such as SQLite files or a temporary directory',
    )
    CONTRACT = 'contract', 'runs the suite of one port on its fake and on every adapter'
    COMPONENT = 'component', 'runs the whole backend in the process through an HTTP client'


# The fixtures that run the application, which only a component test may take
APP_FIXTURES: frozenset[str] = frozenset({'fx_app', 'fx_client'})
# The fixtures each level refuses: the disk, the SQLite files built on it and the application. A fixture taken
# through another one counts, since pytest lists the whole closure of a test
REFUSED_FIXTURES: Mapping[Level, frozenset[str]] = {
    Level.UNIT: frozenset(
        {'tmp_path', 'tmp_path_factory', 'fx_schema_template', 'fx_book_template', 'fx_database', 'fx_books'}
    )
    | APP_FIXTURES,
    Level.INTEGRATION: APP_FIXTURES,
}
LEVEL_KEY: pytest.StashKey[Level] = pytest.StashKey[Level]()


def pytest_configure(config: pytest.Config) -> None:
    """Register the marker of every level, so that ``--strict-markers`` accepts it.

    :param config: Configuration of the run.
    :type config: pytest.Config
    """
    for level in Level:
        config.addinivalue_line('markers', f'{level}: {level.label}')


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Mark every collected test with the level of the directory under ``tests`` that holds it.

    :param config: Configuration of the run, whose root holds the directory of the tests.
    :type config: pytest.Config
    :param items: The collected tests.
    :type items: list[pytest.Item]
    :raises pytest.UsageError: When a test lies outside the directories of the levels.
    """
    tests_dir = config.rootpath / TESTS_DIR
    for item in items:
        parts = item.path.relative_to(tests_dir).parts if item.path.is_relative_to(tests_dir) else ()
        if not parts or parts[0] not in Level:
            levels = NAME_SEPARATOR.join(Level)
            err_msg = f'{item.nodeid} lies outside the directories of the levels ({levels}) under {tests_dir}'
            raise pytest.UsageError(err_msg)
        item.stash[LEVEL_KEY] = Level(parts[0])
        item.add_marker(item.stash[LEVEL_KEY])


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    """Fail a test at setup, before any of its fixtures runs, when it takes a fixture that its level refuses.

    :param item: The test about to be set up.
    :type item: pytest.Item
    """
    if not isinstance(item, pytest.Function):
        return
    level = item.stash[LEVEL_KEY]
    if refused := sorted(REFUSED_FIXTURES.get(level, frozenset()) & set(item.fixturenames)):
        pytest.fail(f'tests/{level} {level.label}, but the test takes {NAME_SEPARATOR.join(refused)}', pytrace=False)
