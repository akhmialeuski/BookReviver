"""Tests for the database provider, which prepares the ORM before the application serves its first request.

SQLAlchemy keeps its mappers in process-wide registries and configures them on the first use of the ORM, so the
mappers of the tables are configured already in any test that runs after another one used the ORM. The test therefore
declares a mapper of its own in a fresh registry right before the start, which nothing but a configuration at the start
can have configured, since ``configure_mappers`` configures every registry of the process.
"""

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import Column, Integer, Table
from sqlalchemy.orm import registry

from bookreviver.app.main import create_app
from tests.helpers.processing import InProcessExecutorProvider
from tests.helpers.schema import create_schema

if TYPE_CHECKING:
    from collections.abc import Iterator

    from bookreviver.app.settings import Settings

pytestmark = pytest.mark.anyio

PROBE_TABLE: str = 'mapper_probe'


class MapperProbe:
    """A class the test maps in its own registry, whose mapper is configured only by a configuration of the process."""


@pytest.fixture
def fx_probe_registry() -> Iterator[registry]:
    """Give a fresh registry of mappers, disposed with every mapper in it after the test.

    :returns: Iterator yielding the registry and disposing of it afterwards.
    :rtype: Iterator[registry]
    """
    probe = registry()
    yield probe
    probe.dispose()


class TestMappersAtStart:
    """Tests that the application leaves every mapper of the process configured when it starts."""

    async def test_start_configures_a_mapper_declared_right_before_it(
        self, fx_settings: Settings, fx_probe_registry: registry
    ) -> None:
        """Verify the start of the application configures a mapper that nothing configured before it.

        :param fx_settings: Settings with a fresh data directory.
        :type fx_settings: Settings
        :param fx_probe_registry: Fresh registry the probe class is mapped in.
        :type fx_probe_registry: registry
        """
        await create_schema(fx_settings)
        table = Table(PROBE_TABLE, fx_probe_registry.metadata, Column('id', Integer, primary_key=True))
        mapper = fx_probe_registry.map_imperatively(MapperProbe, table)
        assert not mapper.configured, (
            'The probe mapper is configured before the start, so the check would prove nothing.'
        )
        app = create_app(fx_settings, [InProcessExecutorProvider()])
        async with app.router.lifespan_context(app):
            assert mapper.configured
