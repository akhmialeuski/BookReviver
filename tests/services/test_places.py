"""Tests for the use cases of the place an account left a book at."""

from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from bookreviver.domain.entities import Actor
from bookreviver.domain.enums import PlaceMode, Stage, ViewMode
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import PageId
from bookreviver.domain.values import CanvasPosition, NewBookPlace
from bookreviver.services.library import Library
from bookreviver.services.places import PlaceService
from tests.helpers.builders import make_project, new_account_id
from tests.helpers.seeding import commit_project

if TYPE_CHECKING:
    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from tests.helpers.clock import FixedClock
    from tests.helpers.database import UnitOfWorkFactory

pytestmark = pytest.mark.anyio

MOVE: timedelta = timedelta(minutes=5)


def _service(new_uow: UnitOfWorkFactory, clock: FixedClock) -> PlaceService:
    """Build the place service for one request.

    :param new_uow: Function opening a new unit of work over the database of the test.
    :type new_uow: UnitOfWorkFactory
    :param clock: Clock stamping the places.
    :type clock: FixedClock
    :returns: The service over a new unit of work.
    :rtype: PlaceService
    """
    uow = new_uow()
    return PlaceService(uow=uow, library=Library(uow=uow), clock=clock)


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


class TestPlaceService:
    """Tests for the place service."""

    async def test_a_book_not_worked_on_has_no_place(
        self, fx_database: SqlDatabase, fx_uow: UnitOfWorkFactory, fx_clock: FixedClock
    ) -> None:
        """Verify the place of a new book is none.

        :param fx_database: Database of the test.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_clock: Clock stopped at the epoch.
        :type fx_clock: FixedClock
        """
        actor = Actor(account_id=new_account_id())
        project = make_project()
        await commit_project(fx_database, fx_uow(), project, owner=actor.account_id)
        places = _service(fx_uow, fx_clock)
        assert await places.find(actor, project.id) is None

    async def test_save_stores_the_place_with_the_time_of_the_write(
        self, fx_database: SqlDatabase, fx_uow: UnitOfWorkFactory, fx_clock: FixedClock
    ) -> None:
        """Verify a saved place is read by a later request, stamped with the clock.

        :param fx_database: Database of the test.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_clock: Clock stopped at the epoch.
        :type fx_clock: FixedClock
        """
        actor = Actor(account_id=new_account_id())
        project = make_project()
        await commit_project(fx_database, fx_uow(), project, owner=actor.account_id)
        new = _place()
        saved = await _service(fx_uow, fx_clock).save(actor, project.id, new)
        found = await _service(fx_uow, fx_clock).find(actor, project.id)
        assert found == saved
        assert (found.updated_at, found.canvas, found.page_id) == (fx_clock.now(), new.canvas, new.page_id)

    async def test_save_replaces_the_place(
        self, fx_database: SqlDatabase, fx_uow: UnitOfWorkFactory, fx_clock: FixedClock
    ) -> None:
        """Verify a later save replaces the earlier one whole, a canvas position included.

        :param fx_database: Database of the test.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_clock: Clock stopped at the epoch.
        :type fx_clock: FixedClock
        """
        actor = Actor(account_id=new_account_id())
        project = make_project()
        await commit_project(fx_database, fx_uow(), project, owner=actor.account_id)
        await _service(fx_uow, fx_clock).save(actor, project.id, _place())
        fx_clock.moment += MOVE
        later = NewBookPlace(mode=PlaceMode.READING, stage=Stage.LAYOUT)
        await _service(fx_uow, fx_clock).save(actor, project.id, later)
        found = await _service(fx_uow, fx_clock).find(actor, project.id)
        assert found is not None
        assert (found.mode, found.stage, found.canvas, found.updated_at) == (
            PlaceMode.READING,
            Stage.LAYOUT,
            None,
            fx_clock.now(),
        )

    async def test_a_save_stamped_before_the_stored_place_changes_nothing(
        self, fx_database: SqlDatabase, fx_uow: UnitOfWorkFactory, fx_clock: FixedClock
    ) -> None:
        """Verify a save stamped before the stored place answers with the stored place and leaves it.

        :param fx_database: Database of the test.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_clock: Clock stopped at the epoch.
        :type fx_clock: FixedClock
        """
        actor = Actor(account_id=new_account_id())
        project = make_project()
        await commit_project(fx_database, fx_uow(), project, owner=actor.account_id)
        older_request = _service(fx_uow, fx_clock)
        newer_request = _service(fx_uow, fx_clock)
        fx_clock.moment += MOVE
        newer = await newer_request.save(actor, project.id, NewBookPlace(mode=PlaceMode.READING, stage=Stage.LAYOUT))
        fx_clock.moment -= MOVE
        answered = await older_request.save(actor, project.id, _place())
        found = await _service(fx_uow, fx_clock).find(actor, project.id)
        assert (answered, found) == (newer, newer)

    async def test_the_book_of_another_account_is_not_found(
        self, fx_database: SqlDatabase, fx_uow: UnitOfWorkFactory, fx_clock: FixedClock
    ) -> None:
        """Verify neither reading nor writing a place of a book of another account says the book exists.

        :param fx_database: Database of the test.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_clock: Clock stopped at the epoch.
        :type fx_clock: FixedClock
        """
        project = make_project()
        await commit_project(fx_database, fx_uow(), project, owner=new_account_id())
        stranger = Actor(account_id=new_account_id())
        places = _service(fx_uow, fx_clock)
        with pytest.raises(NotFoundError):
            await places.find(stranger, project.id)
        with pytest.raises(NotFoundError):
            await places.save(stranger, project.id, _place())
        assert await fx_uow().read_book(project.id, lambda repos: repos.book_places.find(project.id)) is None
