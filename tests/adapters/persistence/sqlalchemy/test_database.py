"""Tests for SqlDatabase: the engine and sessions advanced-alchemy builds keep what the domain writes."""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve

from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from tests.helpers.builders import make_project

if TYPE_CHECKING:
    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.ids import AccountId

pytestmark = pytest.mark.anyio


class TestSqlDatabase:
    """Tests for the sessions SqlDatabase opens."""

    async def test_update_stores_the_updated_at_of_the_domain(
        self, fx_database: SqlDatabase, fx_owner_id: AccountId
    ) -> None:
        """Verify a project updated with a new title keeps the ``updated_at`` the domain gave it, and no other.

        advanced-alchemy's touch listener, on by default, replaces an ``updated_at`` the flush does not change with
        the wall clock, which is not the ``Clock`` of the domain.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        """
        project = make_project(owner_id=fx_owner_id)
        renamed = evolve(project, details=evolve(project.details, title='Renamed'))
        async with fx_database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            await uow.projects.add(project)
            await uow.commit()
        async with fx_database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            await uow.projects.update(renamed)
            await uow.commit()
        async with fx_database.sessions() as session:
            stored = await SqlAlchemyUnitOfWork(session).projects.get(project.id)
        assert stored.updated_at == renamed.updated_at
