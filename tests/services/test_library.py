"""Tests for the library, which is the one place that checks an account may work on a book."""

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import ProjectId
from bookreviver.services.library import Library
from tests.helpers.builders import make_project, new_account_id
from tests.helpers.seeding import commit_project, store_project

if TYPE_CHECKING:
    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import Actor
    from tests.helpers.database import UnitOfWorkFactory

pytestmark = pytest.mark.anyio


class TestOpen:
    """Tests for Library.open()."""

    async def test_opens_a_book_of_the_actor(self, fx_uow: UnitOfWorkFactory, fx_actor: Actor) -> None:
        """Verify the entry of a book of the actor is returned, with the owner and the time the book joined.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the library opens books for.
        :type fx_actor: Actor
        """
        project = make_project(minutes=3)
        await store_project(fx_uow(), project, owner=fx_actor.account_id)

        entry = await Library(uow=fx_uow()).open(fx_actor, project.id)

        assert (entry.project_id, entry.owner_id, entry.added_at) == (
            project.id,
            fx_actor.account_id,
            project.created_at,
        )

    async def test_a_book_of_another_account_is_not_found(
        self, fx_database: SqlDatabase, fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify a book of another account answers like a missing book, so nobody learns that it exists.

        :param fx_database: Catalogue of the test.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the library opens books for.
        :type fx_actor: Actor
        """
        project = make_project()
        await commit_project(fx_database, fx_uow(), project, owner=new_account_id())

        with pytest.raises(NotFoundError) as refused:
            await Library(uow=fx_uow()).open(fx_actor, project.id)

        missing_id = ProjectId(uuid4())
        with pytest.raises(NotFoundError) as missing:
            await Library(uow=fx_uow()).open(fx_actor, missing_id)
        assert str(refused.value).replace(str(project.id), '') == str(missing.value).replace(str(missing_id), '')

    async def test_a_book_that_is_not_listed_is_not_found(self, fx_uow: UnitOfWorkFactory, fx_actor: Actor) -> None:
        """Verify a book that has a store and no entry, which is a creation broken off half way, cannot be opened.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the library opens books for.
        :type fx_actor: Actor
        """
        project = make_project()
        await fx_uow().create_book(project)

        with pytest.raises(NotFoundError):
            await Library(uow=fx_uow()).open(fx_actor, project.id)
