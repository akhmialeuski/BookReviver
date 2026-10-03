"""Tests of two requests that change one page at once on SQLAlchemy, where only the revision of the row can tell.

The page service runs over a unit of work whose page repository lets a rival request commit a change to the same row
right after the service read it. The rival has a session of its own on the same SQLite database, so the service writes
over a page that is stale by the time of its update, which is the interleaving of two requests in the real application.
"""

from typing import TYPE_CHECKING, override

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.clock.system import FixedClock
from bookreviver.adapters.jobs.recording import RecordingJobQueue
from bookreviver.adapters.persistence.sqlalchemy.repositories import SqlAlchemyPageRepository
from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from bookreviver.adapters.storage import LocalAssetStore
from bookreviver.domain.changes import PageChanges
from bookreviver.domain.entities import Actor
from bookreviver.domain.enums import PageKind
from bookreviver.domain.errors import ConcurrentChangeError
from bookreviver.domain.values import SliceRequest
from tests.helpers.builders import EPOCH, make_page, make_project
from tests.helpers.fakes_jobs import RecordingEventBus
from tests.helpers.page_services import make_page_service

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import Page
    from bookreviver.domain.ids import AccountId, PageId
    from bookreviver.services.pages import PageService

pytestmark = pytest.mark.anyio

STALE_LABEL: str = 'old'
RIVAL_LABEL: str = 'rival'
PAGE_COUNT: int = 3
ORDER_KEYS: tuple[str, ...] = ('a0', 'a1', 'a2')


class RacingPages(SqlAlchemyPageRepository):
    """Page repository that awaits a rival request after the service has read a page.

    :ivar reads: How many reads of one page the service has made.
    """

    def __init__(self, session: AsyncSession, rival: Callable[[], Awaitable[None]]) -> None:
        """Bind the repository to the session of the service, and the rival to the reads that give it its turn.

        :param session: Session of the service's unit of work.
        :type session: AsyncSession
        :param rival: Request that commits its change, awaited after each read of one page.
        :type rival: Callable[[], Awaitable[None]]
        """
        super().__init__(session)
        self._rival = rival
        self.reads = 0

    @override
    async def get(self, entity_id: PageId) -> Page:
        """Read a page, then give the rival its turn.

        :param entity_id: Identifier of the page.
        :type entity_id: PageId
        :returns: The page as it was before the rival wrote.
        :rtype: Page
        """
        page = await super().get(entity_id)
        self.reads += 1
        await self._rival()
        return page


class Race:
    """A book of three pages on SQLite, a service that reads it, and a rival that rewrites a page the service read.

    :ivar actor: The account owning the book.
    :ivar pages: The stored pages in book order.
    :ivar rival_writes: How many times the rival has committed its change.
    :ivar racing: The page repository of the last service built, whose reads are counted.
    """

    def __init__(self, database: SqlDatabase, owner_id: AccountId, storage: Path, *, rival_writes: int) -> None:
        """Prepare the rig; the book is stored by ``store``.

        :param database: Fresh SQLite database with every table created.
        :type database: SqlDatabase
        :param owner_id: Committed account owning the project.
        :type owner_id: AccountId
        :param storage: Storage root of the asset store.
        :type storage: Path
        :param rival_writes: How many times the rival commits its change before it lets the service alone.
        :type rival_writes: int
        """
        self._database = database
        self.actor = Actor(account_id=owner_id)
        self._storage = storage
        self._rival_budget = rival_writes
        self.project = make_project(owner_id=owner_id)
        self.pages = [
            evolve(make_page(project_id=self.project.id, order_key=key), label=STALE_LABEL) for key in ORDER_KEYS
        ]
        self.rival_writes = 0
        self.racing: RacingPages | None = None

    async def store(self) -> None:
        """Commit the project and its pages."""
        async with self._database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            await uow.projects.add(self.project)
            await uow.pages.add_many(self.pages)
            await session.commit()

    async def rival(self) -> None:
        """Commit a new label of the middle page in a session of its own, while the budget lasts."""
        if self.rival_writes == self._rival_budget:
            return
        self.rival_writes += 1
        async with self._database.sessions() as session:
            pages = SqlAlchemyUnitOfWork(session).pages
            await pages.update(evolve(await pages.get(self.pages[1].id), label=f'{RIVAL_LABEL}{self.rival_writes}'))
            await session.commit()

    def service(self, session: AsyncSession) -> PageService:
        """Build the page service over a unit of work whose page reads give the rival a turn.

        :param session: Session of the request.
        :type session: AsyncSession
        :returns: The service of one request.
        :rtype: PageService
        """
        uow = SqlAlchemyUnitOfWork(session)
        self.racing = RacingPages(session, self.rival)
        uow.pages = self.racing
        runtime = (RecordingEventBus(), FixedClock(EPOCH), RecordingJobQueue())
        return make_page_service(uow, LocalAssetStore(root=self._storage), runtime)

    async def stored(self) -> list[Page]:
        """Read the pages of the book as committed.

        :returns: The pages in book order.
        :rtype: list[Page]
        """
        async with self._database.sessions() as session:
            return list(
                (await SqlAlchemyUnitOfWork(session).pages.list_for_project(self.project.id, SliceRequest())).items
            )


async def _race(database: SqlDatabase, owner_id: AccountId, tmp_path: Path, *, rival_writes: int) -> Race:
    """Store a book and return the rig that races a rival against the service.

    :param database: Fresh SQLite database with every table created.
    :type database: SqlDatabase
    :param owner_id: Committed account owning the project.
    :type owner_id: AccountId
    :param tmp_path: Temporary directory of the test.
    :type tmp_path: Path
    :param rival_writes: How many times the rival commits its change.
    :type rival_writes: int
    :returns: The rig, with its book stored.
    :rtype: Race
    """
    race = Race(database, owner_id, tmp_path / 'storage', rival_writes=rival_writes)
    await race.store()
    return race


class TestUpdateOnSqlAlchemy:
    """Tests for PageService.update() against a row another request changes after it was read."""

    async def test_two_edits_of_different_fields_both_survive(
        self, fx_database: SqlDatabase, fx_owner_id: AccountId, tmp_path: Path
    ) -> None:
        """Verify the label a rival wrote after the read is kept when the service changes the kind of the page.

        The service reads the page, the rival commits its label, and the update of the service finds the revision
        raised, rolls back and applies its kind to the page the rival left, so neither edit is lost.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        race = await _race(fx_database, fx_owner_id, tmp_path, rival_writes=1)

        async with fx_database.sessions() as session:
            updated = await race.service(session).update(
                race.actor, race.project.id, race.pages[1].id, PageChanges(kind=PageKind.PLATE)
            )

        [_, middle, _] = await race.stored()
        expect(race.rival_writes == 1)
        expect(race.racing is not None and race.racing.reads == 2)
        expect((middle.label, middle.kind) == (f'{RIVAL_LABEL}1', PageKind.PLATE))
        expect(updated.page.label == f'{RIVAL_LABEL}1')
        expect(middle.revision == 2)
        assert_expectations()

    async def test_a_page_that_never_stops_changing_conflicts_after_three_attempts(
        self, fx_database: SqlDatabase, fx_owner_id: AccountId, tmp_path: Path
    ) -> None:
        """Verify a rival that wins every attempt makes the update give up with the readable conflict.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        race = await _race(fx_database, fx_owner_id, tmp_path, rival_writes=PAGE_COUNT)

        async with fx_database.sessions() as session:
            service = race.service(session)
            with pytest.raises(ConcurrentChangeError):
                await service.update(race.actor, race.project.id, race.pages[1].id, PageChanges(kind=PageKind.PLATE))

        [_, middle, _] = await race.stored()
        expect(race.racing is not None and race.racing.reads == PAGE_COUNT)
        expect((middle.label, middle.kind) == (f'{RIVAL_LABEL}{PAGE_COUNT}', race.pages[1].kind))
        assert_expectations()
