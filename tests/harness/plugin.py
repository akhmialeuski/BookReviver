"""The pytest plugin of the suite, which marks every test with the level of the directory that holds it.

The levels are the directories right under ``tests``, so ``-m unit`` selects the tests of ``tests/unit`` and nothing
else. A test anywhere else has no level, so its collection stops the run instead of leaving it unmarked. The plugin
also loads the fixtures that tests of several levels share, and pytester for its own tests.
"""

import pytest

from bookreviver.domain.labels import LabeledStrEnum

pytest_plugins = (
    'pytester',
    'tests.harness.fixtures.anyio_backend',
    'tests.harness.fixtures.settings',
    'tests.harness.fixtures.databases',
)

# The directory under the root of the project that holds one directory per level
TESTS_DIR: str = 'tests'


class Level(LabeledStrEnum):
    """Level of a test, which is the name of the directory under ``tests`` that holds it."""

    UNIT = 'unit', 'runs in memory, with no file, process, clock or database'
    INTEGRATION = (
        'integration',
        'uses one real technology in the process, such as SQLite files or a temporary directory',
    )
    CONTRACT = 'contract', 'runs the suite of one port on its fake and on every adapter'
    COMPONENT = 'component', 'runs the whole backend in the process through an HTTP client'


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
            err_msg = f'{item.nodeid} lies outside the directories of the levels ({", ".join(Level)}) under {tests_dir}'
            raise pytest.UsageError(err_msg)
        item.add_marker(Level(parts[0]))
