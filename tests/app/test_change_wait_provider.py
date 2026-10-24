"""Tests for the limit of a catalogue database the container chooses by the context of the scope the catalogue database lives in."""

from typing import TYPE_CHECKING, Any

import pytest

from bookreviver.app.container import build_container
from bookreviver.app.settings import ChangeWaitSettings
from bookreviver.ports.persistence import ChangeWaitSeconds

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from dishka import AsyncContainer

    from bookreviver.app.settings import Settings

pytestmark = pytest.mark.anyio

# Two limits that differ from each other and from the defaults, so a test cannot pass on a default by chance
REQUEST_SECONDS: float = 1.25
JOB_SECONDS: float = 17.0


class TestChangeWaitProvider:
    """Tests for ChangeWaitProvider: the limit follows the context of the scope, never a flag the caller passes."""

    @pytest.fixture
    async def fx_container(self, fx_settings: Settings) -> AsyncIterator[AsyncContainer]:
        """Build the application's container with two different limits in its settings.

        :param fx_settings: Settings of the suite.
        :type fx_settings: Settings
        :returns: Iterator yielding the container and closing it afterwards.
        :rtype: AsyncIterator[AsyncContainer]
        """
        limits = ChangeWaitSettings(request_seconds=REQUEST_SECONDS, job_seconds=JOB_SECONDS)
        container = build_container(fx_settings.model_copy(update={'change_wait': limits}))
        try:
            yield container
        finally:
            await container.close()

    async def test_scope_with_a_request_gets_the_limit_of_a_request(
        self, fx_container: AsyncContainer, fx_request_context: dict[Any, Any]
    ) -> None:
        """Verify a scope that has a ``Request`` in its context, as every HTTP request has, gets ``request_seconds``.

        :param fx_container: Container of the test.
        :type fx_container: AsyncContainer
        :param fx_request_context: Context of the scope of an HTTP request.
        :type fx_request_context: dict[Any, Any]
        """
        async with fx_container(context=fx_request_context) as scope:
            assert await scope.get(ChangeWaitSeconds) == REQUEST_SECONDS

    async def test_scope_without_a_context_gets_the_limit_of_a_job(self, fx_container: AsyncContainer) -> None:
        """Verify a bare scope with no ``Request``, as every background job and the startup in the lifespan have, gets ``job_seconds``.

        :param fx_container: Container of the test.
        :type fx_container: AsyncContainer
        """
        async with fx_container() as scope:
            assert await scope.get(ChangeWaitSeconds) == JOB_SECONDS
