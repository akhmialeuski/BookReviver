"""Fixtures of the API tests: the dependency container and the stored files of the running application.

The database is the root ``fx_database``, which the application runs on as well.
"""

from typing import TYPE_CHECKING

import pytest
from dishka import AsyncContainer

from bookreviver.api.auth import signed_in_user
from tests.helpers.board_idle import JobsIdle
from tests.helpers.storage import BookFiles

if TYPE_CHECKING:
    from fastapi import FastAPI

    from bookreviver.app.settings import Settings


@pytest.fixture
def fx_signed_out(fx_app: FastAPI) -> None:
    """Take the test's sign-in off, so a request has no session.

    :param fx_app: The running application, whose stub user is removed.
    :type fx_app: FastAPI
    """
    fx_app.dependency_overrides.pop(signed_in_user)


@pytest.fixture
def fx_container(fx_app: FastAPI) -> AsyncContainer:
    """Return the dependency container of the running application.

    :param fx_app: The running application, on whose state dishka keeps its container.
    :type fx_app: FastAPI
    :returns: The application's container, holding its adapters.
    :rtype: AsyncContainer
    """
    container = fx_app.state.dishka_container
    assert isinstance(container, AsyncContainer)
    return container


@pytest.fixture
def fx_idle() -> JobsIdle:
    """Return the wait that a test calls to let the jobs of a book on the application's job board finish.

    The wait opens the job queue and the event channel of the book, which the running application registered.

    :returns: The wait, called with the identifier of the book.
    :rtype: JobsIdle
    """
    return JobsIdle()


@pytest.fixture
def fx_files(fx_container: AsyncContainer, fx_settings: Settings) -> BookFiles:
    """Return the files of imported books in the stores of the running application.

    :param fx_container: Container of the application, which has registered its local storage by the time it runs.
    :type fx_container: AsyncContainer
    :param fx_settings: Settings of the application, which name the storage root.
    :type fx_settings: Settings
    :returns: Files of the test's books, in the application's own stores.
    :rtype: BookFiles
    """
    del fx_container
    return BookFiles(root=fx_settings.storage_root)
