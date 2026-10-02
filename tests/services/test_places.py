"""Tests for the use cases of the place an account left a book at, against in-memory persistence."""

from datetime import timedelta
from typing import TYPE_CHECKING, override
from uuid import uuid4

import pytest

from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.adapters.persistence.memory.unit_of_work import InMemoryBookPlaceRepository
from bookreviver.domain.entities import Actor
from bookreviver.domain.enums import PlaceMode, Stage, ViewMode
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.ids import PageId
from bookreviver.domain.values import BookPlaceKey, CanvasPosition, NewBookPlace
from bookreviver.services.places import PlaceService
from tests.helpers.builders import make_project, new_account_id
from tests.helpers.seeding import commit_project

if TYPE_CHECKING:
    from bookreviver.adapters.clock.system import FixedClock
    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.adapters.persistence.memory.unit_of_work import InMemoryTables
    from bookreviver.domain.entities import BookPlace

pytestmark = pytest.mark.anyio

MOVE: timedelta = timedelta(minutes=5)


def _place(stage: Stage = Stage.GEOMETRY) -> NewBookPlace:
    """Build a place of a reader who zoomed into a page of a stage in the spread layout.

    :param stage: Stage the reader is on.
    :type stage: Stage
    :returns: The place a client reports.
    :rtype: NewBookPlace
    """
    return NewBookPlace(
        mode=PlaceMode.WORKSPACE,
        stage=stage,
        page_id=PageId(uuid4()),
        view=ViewMode.SPREAD,
        canvas=CanvasPosition(zoom=3.0, centre_x=0.5, centre_y=0.25),
    )


class LosingPlaces(InMemoryBookPlaceRepository):
    """A place repository that refuses some saves with the conflict a database raises when another request won."""

    def __init__(self, tables: InMemoryTables, losses: list[int]) -> None:
        """Work on the tables of a unit of work, refusing the given number of saves.

        :param tables: Every table of the working copy.
        :type tables: InMemoryTables
        :param losses: One number, the saves still to refuse, shared by every transaction of the test.
        :type losses: list[int]
        """
        super().__init__(tables)
        self._losses = losses

    @override
    async def save(self, place: BookPlace) -> BookPlace:
        """Refuse the save while losses remain, and store the place after that.

        :param place: Place to store.
        :type place: BookPlace
        :returns: The place as stored.
        :rtype: BookPlace
        :raises ConflictError: While saves remain to be refused.
        """
        if self._losses[0] > 0:
            self._losses[0] -= 1
            raise ConflictError(place.key)
        return await super().save(place)


class RacingUnitOfWork(InMemoryUnitOfWork):
    """A unit of work whose place repository loses the first saves, in every transaction it begins."""

    def __init__(self, database: InMemoryDatabase, losses: list[int]) -> None:
        """Begin a transaction over ``database`` whose saves of a place are refused ``losses`` times.

        :param database: Committed state shared with every other unit of work.
        :type database: InMemoryDatabase
        :param losses: One number, the saves still to refuse.
        :type losses: list[int]
        """
        self._losses = losses
        super().__init__(database)

    @override
    def _begin(self) -> None:
        """Begin a transaction, with the place repository that loses saves."""
        super()._begin()
        self.book_places = LosingPlaces(self._tables, self._losses)


class TestPlaceService:
    """Tests for the place service."""

    async def test_a_book_not_worked_on_has_no_place(self, fx_database: InMemoryDatabase, fx_clock: FixedClock) -> None:
        """Verify the place of a new book is none.

        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_clock: Clock stopped at the epoch.
        :type fx_clock: FixedClock
        """
        actor = Actor(account_id=new_account_id())
        project = make_project(owner_id=actor.account_id)
        await commit_project(fx_database, project)
        places = PlaceService(uow=InMemoryUnitOfWork(fx_database), clock=fx_clock)
        assert await places.find(actor, project.id) is None

    async def test_save_stores_the_place_with_the_time_of_the_write(
        self, fx_database: InMemoryDatabase, fx_clock: FixedClock
    ) -> None:
        """Verify a saved place is read by a later request, stamped with the clock.

        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_clock: Clock stopped at the epoch.
        :type fx_clock: FixedClock
        """
        actor = Actor(account_id=new_account_id())
        project = make_project(owner_id=actor.account_id)
        await commit_project(fx_database, project)
        new = _place()
        saved = await PlaceService(uow=InMemoryUnitOfWork(fx_database), clock=fx_clock).save(actor, project.id, new)
        found = await PlaceService(uow=InMemoryUnitOfWork(fx_database), clock=fx_clock).find(actor, project.id)
        assert found == saved
        assert (found.updated_at, found.canvas, found.page_id) == (fx_clock.now(), new.canvas, new.page_id)

    async def test_save_replaces_the_place(self, fx_database: InMemoryDatabase, fx_clock: FixedClock) -> None:
        """Verify a later save replaces the earlier one whole, a canvas position included.

        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_clock: Clock stopped at the epoch.
        :type fx_clock: FixedClock
        """
        actor = Actor(account_id=new_account_id())
        project = make_project(owner_id=actor.account_id)
        await commit_project(fx_database, project)
        await PlaceService(uow=InMemoryUnitOfWork(fx_database), clock=fx_clock).save(actor, project.id, _place())
        fx_clock.moment += MOVE
        later = NewBookPlace(mode=PlaceMode.READING, stage=Stage.LAYOUT)
        await PlaceService(uow=InMemoryUnitOfWork(fx_database), clock=fx_clock).save(actor, project.id, later)
        found = await PlaceService(uow=InMemoryUnitOfWork(fx_database), clock=fx_clock).find(actor, project.id)
        assert found is not None
        assert (found.mode, found.stage, found.canvas, found.updated_at) == (
            PlaceMode.READING,
            Stage.LAYOUT,
            None,
            fx_clock.now(),
        )

    async def test_save_stores_again_after_losing_a_race_for_the_first_place(
        self, fx_database: InMemoryDatabase, fx_clock: FixedClock
    ) -> None:
        """Verify a save that the database refuses because another request stored the first place is repeated once.

        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_clock: Clock stopped at the epoch.
        :type fx_clock: FixedClock
        """
        actor = Actor(account_id=new_account_id())
        project = make_project(owner_id=actor.account_id)
        await commit_project(fx_database, project)
        saved = await PlaceService(uow=RacingUnitOfWork(fx_database, [1]), clock=fx_clock).save(
            actor, project.id, _place()
        )
        stored = await InMemoryUnitOfWork(fx_database).book_places.find(BookPlaceKey(actor.account_id, project.id))
        assert stored == saved

    async def test_save_gives_up_after_losing_the_race_twice(
        self, fx_database: InMemoryDatabase, fx_clock: FixedClock
    ) -> None:
        """Verify a conflict that outlasts the repeat is reported and stores nothing.

        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_clock: Clock stopped at the epoch.
        :type fx_clock: FixedClock
        """
        actor = Actor(account_id=new_account_id())
        project = make_project(owner_id=actor.account_id)
        await commit_project(fx_database, project)
        places = PlaceService(uow=RacingUnitOfWork(fx_database, [2]), clock=fx_clock)
        with pytest.raises(ConflictError):
            await places.save(actor, project.id, _place())
        assert (
            await InMemoryUnitOfWork(fx_database).book_places.find(BookPlaceKey(actor.account_id, project.id)) is None
        )

    async def test_the_book_of_another_account_is_not_found(
        self, fx_database: InMemoryDatabase, fx_clock: FixedClock
    ) -> None:
        """Verify neither reading nor writing a place of a book of another account says the book exists.

        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_clock: Clock stopped at the epoch.
        :type fx_clock: FixedClock
        """
        project = make_project(owner_id=new_account_id())
        await commit_project(fx_database, project)
        stranger = Actor(account_id=new_account_id())
        places = PlaceService(uow=InMemoryUnitOfWork(fx_database), clock=fx_clock)
        with pytest.raises(NotFoundError):
            await places.find(stranger, project.id)
        with pytest.raises(NotFoundError):
            await places.save(stranger, project.id, _place())
        key = BookPlaceKey(stranger.account_id, project.id)
        assert await InMemoryUnitOfWork(fx_database).book_places.find(key) is None
