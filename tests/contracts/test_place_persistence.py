"""Contract of the persistence port of the places accounts left books at.

Every test runs against the SQL adapter, so it keeps the promises of the port: one place for each account and book, a
replacement on every save that is whole, and the removal of the places with the book.
"""

from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from attrs import evolve

from bookreviver.domain.enums import PlaceMode, Stage
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import PageId
from bookreviver.domain.values import BookPlaceKey, CanvasPosition
from tests.helpers.builders import make_book_place, make_project
from tests.helpers.seeding import store_project

if TYPE_CHECKING:
    from bookreviver.domain.entities import BookPlace
    from bookreviver.domain.ids import ProjectId
    from bookreviver.ports.persistence import Repositories
    from tests.contracts.conftest import OwnerFactory
    from tests.helpers.database import UnitOfWorkFactory

pytestmark = pytest.mark.anyio

# How much later one write of a place is stamped than another
LATER: timedelta = timedelta(minutes=5)


async def _store_project(uow_factory: UnitOfWorkFactory, new_owner: OwnerFactory) -> ProjectId:
    """Store a project in its own change.

    :param uow_factory: Function opening a new unit of work over the database of the test.
    :type uow_factory: UnitOfWorkFactory
    :param new_owner: Function creating an account the database accepts as an owner.
    :type new_owner: OwnerFactory
    :returns: The identifier of the project.
    :rtype: ProjectId
    """
    project = make_project(owner_id=await new_owner())
    await store_project(uow_factory(), project)
    return project.id


class TestBookPlaceRepository:
    """Tests for the places accounts left books at."""

    async def test_save_stores_a_place_with_every_field(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a place reads back whole, with its canvas position and the page of the strip.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id = await _store_project(fx_uow, fx_new_owner)
        place = make_book_place(account_id=await fx_new_owner(), project_id=project_id, page_id=PageId(uuid4()))
        assert await fx_uow().change(lambda repos: repos.book_places.save(place)) == place
        assert await fx_uow().read(lambda repos: repos.book_places.find(place.key)) == place

    async def test_a_place_without_a_canvas_position_reads_back_without_one(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a reader who has not zoomed leaves no canvas position, and a reading place keeps its stage.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id = await _store_project(fx_uow, fx_new_owner)
        place = evolve(
            make_book_place(account_id=await fx_new_owner(), project_id=project_id, stage=Stage.PAGE_ORDER),
            mode=PlaceMode.READING,
            canvas=None,
            strip_page_id=None,
        )
        await fx_uow().change(lambda repos: repos.book_places.save(place))
        assert await fx_uow().read(lambda repos: repos.book_places.get(place.key)) == place

    async def test_save_replaces_the_place_of_the_same_account_and_book(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a second save replaces the first and leaves one place.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id = await _store_project(fx_uow, fx_new_owner)
        first = make_book_place(account_id=await fx_new_owner(), project_id=project_id)
        second = evolve(first, stage=Stage.CLEANUP, page_id=PageId(uuid4()), canvas=None)
        await fx_uow().change(lambda repos: repos.book_places.save(first))
        await fx_uow().change(lambda repos: repos.book_places.save(second))
        assert await fx_uow().read(lambda repos: repos.book_places.find(first.key)) == second

    async def test_successive_saves_leave_the_later_place_whole(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the later of two places saved one after the other is stored whole, the mode it shares with the old one too.

        The stored place and the later one agree on the mode, and the earlier one differs from both, so a store that
        wrote only the columns differing from the place it last read would leave the mode of the earlier place.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id = await _store_project(fx_uow, fx_new_owner)
        account_id = await fx_new_owner()
        stored = evolve(make_book_place(account_id=account_id, project_id=project_id), mode=PlaceMode.READING)
        first = evolve(
            stored, mode=PlaceMode.WORKSPACE, page_id=None, canvas=None, updated_at=stored.updated_at + LATER
        )
        second = evolve(
            stored,
            page_id=PageId(uuid4()),
            canvas=CanvasPosition(zoom=1.0, centre_x=0.4348, centre_y=0.5),
            updated_at=stored.updated_at + 2 * LATER,
        )
        await fx_uow().change(lambda repos: repos.book_places.save(stored))
        assert await fx_uow().read(lambda repos: repos.book_places.find(stored.key)) == stored
        await fx_uow().change(lambda repos: repos.book_places.save(first))
        await fx_uow().change(lambda repos: repos.book_places.save(second))
        assert await fx_uow().read(lambda repos: repos.book_places.find(stored.key)) == second

    async def test_save_of_an_older_place_leaves_the_newer_one(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a write stamped before the stored place changes nothing and answers with the stored place.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id = await _store_project(fx_uow, fx_new_owner)
        older = make_book_place(account_id=await fx_new_owner(), project_id=project_id)
        newer = evolve(older, stage=Stage.CLEANUP, page_id=PageId(uuid4()), updated_at=older.updated_at + LATER)
        await fx_uow().change(lambda repos: repos.book_places.save(newer))
        assert await fx_uow().change(lambda repos: repos.book_places.save(older)) == newer
        assert await fx_uow().read(lambda repos: repos.book_places.find(older.key)) == newer

    async def test_places_of_two_accounts_in_one_book_are_apart(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify two accounts keep their own place in one book.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id = await _store_project(fx_uow, fx_new_owner)
        mine = make_book_place(account_id=await fx_new_owner(), project_id=project_id, stage=Stage.GEOMETRY)
        theirs = make_book_place(account_id=await fx_new_owner(), project_id=project_id, stage=Stage.LAYOUT)

        def save_both(repos: Repositories) -> None:
            """Save the two places.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            """
            repos.book_places.save(mine)
            repos.book_places.save(theirs)

        def find_both(repos: Repositories) -> tuple[BookPlace | None, BookPlace | None]:
            """Read the two places.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :returns: The place of the first account and the place of the second.
            :rtype: tuple[BookPlace | None, BookPlace | None]
            """
            return repos.book_places.find(mine.key), repos.book_places.find(theirs.key)

        await fx_uow().change(save_both)
        assert await fx_uow().read(find_both) == (mine, theirs)

    async def test_find_gives_none_for_a_book_not_worked_on(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify an account that never worked on a book has no place in it.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id = await _store_project(fx_uow, fx_new_owner)
        key = BookPlaceKey(await fx_new_owner(), project_id)
        assert await fx_uow().read(lambda repos: repos.book_places.find(key)) is None

    async def test_get_raises_for_a_book_not_worked_on(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify ``get`` names the missing key where ``find`` gives none.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id = await _store_project(fx_uow, fx_new_owner)
        key = BookPlaceKey(await fx_new_owner(), project_id)
        with pytest.raises(NotFoundError):
            await fx_uow().read(lambda repos: repos.book_places.get(key))

    async def test_save_refuses_a_place_in_a_book_that_is_not_stored(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a place needs its book.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        place = make_book_place(account_id=await fx_new_owner(), project_id=project.id)
        with pytest.raises(NotFoundError):
            await fx_uow().change(lambda repos: repos.book_places.save(place))

    async def test_deleting_a_book_removes_its_places(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the places in a book are removed with it, whichever account they belong to.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id = await _store_project(fx_uow, fx_new_owner)
        place = make_book_place(account_id=await fx_new_owner(), project_id=project_id)
        await fx_uow().change(lambda repos: repos.book_places.save(place))
        await fx_uow().change(lambda repos: repos.projects.delete(project_id))
        assert await fx_uow().read(lambda repos: repos.book_places.find(place.key)) is None
