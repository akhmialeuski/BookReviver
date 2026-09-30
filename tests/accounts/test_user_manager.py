"""Tests for the rules of the user manager that reach beyond the account tables, on the SQL backend."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
from bookreviver.app.container import build_container
from bookreviver.app.providers.accounts import UserManager
from bookreviver.app.settings import PersistenceBackend
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import AccountId
from bookreviver.ports.persistence import UnitOfWork
from bookreviver.ports.storage import AssetStore, SourceStore
from tests.helpers.builders import make_page, make_project
from tests.helpers.schema import create_schema
from tests.helpers.seeding import commit_account
from tests.helpers.storage import BookFiles

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from dishka import AsyncContainer

    from bookreviver.app.settings import Settings
    from bookreviver.domain.entities import Project

pytestmark = pytest.mark.anyio

# More projects than one window of the owner's project list, so a deletion has to page through them
OWNED_PROJECTS: int = 3


@pytest.fixture
async def fx_container(fx_settings: Settings) -> AsyncIterator[AsyncContainer]:
    """Yield the application's container on the SQL backend, where the owner key of a project is checked.

    :param fx_settings: Settings pointing at a fresh data directory of the test.
    :type fx_settings: Settings
    :returns: Iterator yielding the container and closing it afterwards.
    :rtype: AsyncIterator[AsyncContainer]
    """
    await create_schema(fx_settings)
    container = build_container(fx_settings.model_copy(update={'persistence': PersistenceBackend.SQLALCHEMY}))
    yield container
    await container.close()


async def _stored(container: AsyncContainer, project: Project) -> bool:
    """Return whether the project's row is still stored.

    :param container: The application's container.
    :type container: AsyncContainer
    :param project: Project to look up.
    :type project: Project
    :returns: True when a new unit of work still finds the project.
    :rtype: bool
    """
    async with container() as scope:
        try:
            await (await scope.get(UnitOfWork)).projects.get(project.id)
        except NotFoundError:
            return False
    return True


class TestDelete:
    """Tests for UserManager.delete(), which runs the on_before_delete hook first."""

    async def test_deleting_an_account_deletes_its_projects_with_their_files(
        self, fx_container: AsyncContainer, fx_settings: Settings
    ) -> None:
        """Verify the account's projects go with their rows and files, then the account, and another's stay.

        The owner key of a project refuses to delete an account that still owns one, so the projects go first.

        :param fx_container: The application's container on the SQL backend.
        :type fx_container: AsyncContainer
        :param fx_settings: Settings of the test, whose storage root the stores share.
        :type fx_settings: Settings
        """
        database = await fx_container.get(SqlDatabase)
        owner_id, neighbour_id = await commit_account(database), await commit_account(database)
        owned = [make_project(owner_id=owner_id, minutes=minute) for minute in range(OWNED_PROJECTS)]
        kept = make_project(owner_id=neighbour_id)
        pages = {project.id: make_page(project_id=project.id) for project in (*owned, kept)}
        async with fx_container() as scope:
            uow = await scope.get(UnitOfWork)
            for project in (*owned, kept):
                await uow.projects.add(project)
                await uow.pages.add(pages[project.id])
            await uow.commit()
        files = BookFiles(
            sources=await fx_container.get(SourceStore),
            assets=await fx_container.get(AssetStore),
            root=fx_settings.storage_root,
        )
        for page in pages.values():
            await files.store(page)

        async with fx_container() as scope:
            manager = await scope.get(UserManager)
            await manager.delete(await manager.get(owner_id))

        async with fx_container() as scope:
            accounts = await scope.get(UserManager)
            expect(await accounts.user_db.get(AccountId(owner_id)) is None)
        expect([await _stored(fx_container, project) for project in owned] == [False] * OWNED_PROJECTS)
        expect(all(files.gone(project.id) for project in owned))
        expect(await _stored(fx_container, kept))
        expect(files.kept(pages[kept.id]))
        assert_expectations()
