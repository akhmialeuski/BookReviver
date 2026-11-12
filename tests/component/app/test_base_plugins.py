"""Tests for the start of the application: it stops when a base plugin, which is always installed, is missing."""

from typing import TYPE_CHECKING

import pytest
from dishka import Provider, Scope, provide

from bookreviver.app.main import create_app
from bookreviver.core.plugins import ProcessorCatalog
from bookreviver.plugins.split.split_none import SplitNone
from tests.harness.builders.processing import InProcessExecutorProvider
from tests.harness.fakes.processing import FakeCatalogue
from tests.harness.fakes.processors import FakeProcessor

if TYPE_CHECKING:
    from bookreviver.app.settings import Settings
    from tests.harness.adapters.database import SharedDatabaseProvider

pytestmark = pytest.mark.anyio

# What the refusal says of the plugin that is missing, which is the label of its role
BLANK_LEAF_WORDS: str = 'blank leaf'


class NoBlankLeafProvider(Provider):
    """A catalogue of plugins that has the copy of a scan and no blank leaf, as a broken installation would."""

    @provide(scope=Scope.APP)
    def processor_catalog(self) -> ProcessorCatalog:
        """Build the catalogue without the plugin of the blank leaf.

        :returns: The catalogue.
        :rtype: ProcessorCatalog
        """
        return FakeCatalogue([SplitNone(), FakeProcessor()])


class TestBasePlugins:
    """Tests for the check of the base plugins before the first request."""

    async def test_the_start_stops_when_a_base_plugin_is_not_installed(
        self, fx_settings: Settings, fx_shared_database: SharedDatabaseProvider
    ) -> None:
        """Verify the lifespan refuses to start and names what no installed plugin does.

        :param fx_settings: Settings of the application.
        :type fx_settings: Settings
        :param fx_shared_database: Provider of the database and the books of the test.
        :type fx_shared_database: SharedDatabaseProvider
        """
        app = create_app(fx_settings, [fx_shared_database, InProcessExecutorProvider(), NoBlankLeafProvider()])
        with pytest.raises(RuntimeError, match=BLANK_LEAF_WORDS):
            async with app.router.lifespan_context(app):
                pass
