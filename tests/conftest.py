"""Load the fixtures that tests of several levels share, which live in tests/harness/fixtures."""

pytest_plugins = (
    'tests.harness.fixtures.anyio_backend',
    'tests.harness.fixtures.settings',
    'tests.harness.fixtures.databases',
)
