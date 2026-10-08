"""Contract of the persistence port of the places accounts left books at.

Every test runs against each adapter registered in the conftest, the in-memory one and the SQL one, so both keep the
promises of the port: one place for each account and book, a replacement on every save that is whole even when two
transactions write together, and the removal of the places with the book.
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

if TYPE_CHECKING:
    from bookreviver.domain.ids import ProjectId
    from tests.contracts.conftest import OwnerFactory, UnitOfWorkFactory

pytestmark = pytest.mark.anyio

# How much later one write of a place is stamped than another
LATER: timedelta = timedelta(minutes=5)


async def _store_project(uow_factory: UnitOfWorkFactory, new_owner: OwnerFactory) -> ProjectId:
    """Store a project and commit.

    :param uow_factory: Function opening a new unit of work of the backend under test.
    :type uow_factory: UnitOfWorkFactory
    :param new_owner: Function creating an account the backend accepts as an owner.
    :type new_owner: OwnerFactory
    :returns: The identifier of the project.
    :rtype: ProjectId
    """
    uow = await uow_factory()
    project = make_project(owner_id=await new_owner())
    await uow.projects.add(project)
    await uow.commit()
    return project.id


class TestBookPlaceRepository:
    """Tests for the places accounts left books at."""

    async def test_save_stores_a_place_with_every_field(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a place reads back whole, with its canvas position and the page of the strip.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id = await _store_project(fx_uow_factory, fx_new_owner)
        place = make_book_place(account_id=await fx_new_owner(), project_id=project_id, page_id=PageId(uuid4()))
        uow = await fx_uow_factory()
        assert await uow.book_places.save(place) == place
        await uow.commit()
        assert await (await fx_uow_factory()).book_places.find(place.key) == place

    async def test_a_place_without_a_canvas_position_reads_back_without_one(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a reader who has not zoomed leaves no canvas position, and a reading place keeps its stage.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id = await _store_project(fx_uow_factory, fx_new_owner)
        place = evolve(
            make_book_place(account_id=await fx_new_owner(), project_id=project_id, stage=Stage.PAGE_ORDER),
            mode=PlaceMode.READING,
            canvas=None,
            strip_page_id=None,
        )
        uow = await fx_uow_factory()
        await uow.book_places.save(place)
        await uow.commit()
        assert await (await fx_uow_factory()).book_places.get(place.key) == place

    async def test_save_replaces_the_place_of_the_same_account_and_book(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a second save replaces the first and leaves one place.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id = await _store_project(fx_uow_factory, fx_new_owner)
        first = make_book_place(account_id=await fx_new_owner(), project_id=project_id)
        second = evolve(first, stage=Stage.CLEANUP, page_id=PageId(uuid4()), canvas=None)
        for place in (first, second):
            uow = await fx_uow_factory()
            await uow.book_places.save(place)
            await uow.commit()
        assert await (await fx_uow_factory()).book_places.find(first.key) == second

    async def test_interleaved_saves_leave_the_later_place_whole(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the later of two places written one after the other is stored whole, the mode it shares with the old one too.

        The stored place and the later one agree on the mode, and the earlier one differs from both, so a store that
        wrote only the columns differing from the place it last read would leave the mode of the earlier place. The two
        writes run one after the other here: the mixture the e2e run showed came from two requests whose read and
        write overlapped inside one save, which only a run of concurrent requests reproduces.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id = await _store_project(fx_uow_factory, fx_new_owner)
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
        seeding = await fx_uow_factory()
        await seeding.book_places.save(stored)
        await seeding.commit()
        uow_first, uow_second = await fx_uow_factory(), await fx_uow_factory()
        assert await uow_second.book_places.find(stored.key) == stored
        await uow_first.book_places.save(first)
        await uow_first.commit()
        await uow_second.book_places.save(second)
        await uow_second.commit()
        assert await (await fx_uow_factory()).book_places.find(stored.key) == second

    async def test_save_of_an_older_place_leaves_the_newer_one(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a write stamped before the stored place changes nothing and answers with the stored place.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id = await _store_project(fx_uow_factory, fx_new_owner)
        older = make_book_place(account_id=await fx_new_owner(), project_id=project_id)
        newer = evolve(older, stage=Stage.CLEANUP, page_id=PageId(uuid4()), updated_at=older.updated_at + LATER)
        seeding = await fx_uow_factory()
        await seeding.book_places.save(newer)
        await seeding.commit()
        late = await fx_uow_factory()
        assert await late.book_places.save(older) == newer
        await late.commit()
        assert await (await fx_uow_factory()).book_places.find(older.key) == newer

    async def test_places_of_two_accounts_in_one_book_are_apart(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify two accounts keep their own place in one book.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id = await _store_project(fx_uow_factory, fx_new_owner)
        mine = make_book_place(account_id=await fx_new_owner(), project_id=project_id, stage=Stage.GEOMETRY)
        theirs = make_book_place(account_id=await fx_new_owner(), project_id=project_id, stage=Stage.LAYOUT)
        uow = await fx_uow_factory()
        await uow.book_places.save(mine)
        await uow.book_places.save(theirs)
        await uow.commit()
        reading = await fx_uow_factory()
        assert (await reading.book_places.find(mine.key), await reading.book_places.find(theirs.key)) == (mine, theirs)

    async def test_find_gives_none_for_a_book_not_worked_on(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify an account that never worked on a book has no place in it.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id = await _store_project(fx_uow_factory, fx_new_owner)
        key = BookPlaceKey(await fx_new_owner(), project_id)
        assert await (await fx_uow_factory()).book_places.find(key) is None

    async def test_get_raises_for_a_book_not_worked_on(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify ``get`` names the missing key where ``find`` gives none.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id = await _store_project(fx_uow_factory, fx_new_owner)
        with pytest.raises(NotFoundError):
            await (await fx_uow_factory()).book_places.get(BookPlaceKey(await fx_new_owner(), project_id))

    async def test_save_refuses_a_place_in_a_book_that_is_not_stored(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a place needs its book.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        place = make_book_place(account_id=await fx_new_owner(), project_id=project.id)
        uow = await fx_uow_factory()
        with pytest.raises(NotFoundError):
            await uow.book_places.save(place)

    async def test_deleting_a_book_removes_its_places(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the places in a book are removed with it, whichever account they belong to.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id = await _store_project(fx_uow_factory, fx_new_owner)
        place = make_book_place(account_id=await fx_new_owner(), project_id=project_id)
        uow = await fx_uow_factory()
        await uow.book_places.save(place)
        await uow.commit()
        uow = await fx_uow_factory()
        await uow.projects.delete(project_id)
        await uow.commit()
        assert await (await fx_uow_factory()).book_places.find(place.key) is None
