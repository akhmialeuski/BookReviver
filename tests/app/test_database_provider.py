"""Tests for the database provider, which prepares the ORM before the application serves its first request.

SQLAlchemy keeps its mappers in one process-wide registry and configures them on the first use of the ORM, so a test
that runs after another one finds them configured already. Without the provider's call the check fails only when it is
the first test of the process to use the ORM, which is how a server starts.
"""

import pytest
from advanced_alchemy.base import DefaultBase

pytestmark = pytest.mark.anyio


class TestMappersAtStart:
    """Tests that the application leaves the mappers of its tables configured when it starts."""

    @pytest.mark.usefixtures('fx_app')
    async def test_started_application_has_no_mapper_left_to_configure(self) -> None:
        """Verify every mapper of the table registry is configured once the lifespan has started, before any request."""
        mappers = list(DefaultBase.registry.mappers)
        assert mappers, 'The registry holds no mapper, so the check would pass on nothing.'
        assert all(mapper.configured for mapper in mappers), [
            mapper.class_.__name__ for mapper in mappers if not mapper.configured
        ]
