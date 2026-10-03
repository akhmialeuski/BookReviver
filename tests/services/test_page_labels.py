"""Tests for the use cases that edit the fields of a page, on in-memory persistence."""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.domain.changes import PageChanges
from bookreviver.domain.enums import PageChange, PageKind
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.events import PagesChanged
from tests.helpers.builders import EPOCH, make_page, make_project, new_account_id
from tests.helpers.page_services import make_page_service
from tests.helpers.seeding import commit_project

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime

    from bookreviver.adapters.clock.system import FixedClock
    from bookreviver.adapters.jobs.recording import RecordingJobQueue
    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.adapters.storage import LocalAssetStore
    from bookreviver.domain.entities import Actor, Page, Project
    from bookreviver.domain.ids import PageId
    from bookreviver.services.pages import PageService
    from tests.helpers.fakes_jobs import RecordingEventBus

pytestmark = pytest.mark.anyio

LATER: datetime = EPOCH.replace(year=EPOCH.year + 1)
STALE_LABEL: str = 'old'
RIVAL_LABEL: str = 'rival'
# Kind and inclusion of the seven pages of the book, which the edits have to tell apart
LAYOUT: list[tuple[PageKind, bool]] = [
    (PageKind.COVER, True),
    (PageKind.TITLE, True),
    (PageKind.TEXT, True),
    (PageKind.PLATE, True),
    (PageKind.TEXT, True),
    (PageKind.TEXT, False),
    (PageKind.TEXT, True),
]


async def _commit_book(database: InMemoryDatabase, owner: Actor) -> tuple[Project, list[Page]]:
    """Commit a book of seven pages of the kinds in ``LAYOUT``, each labelled by hand with a stale number.

    :param database: In-memory database to commit into.
    :type database: InMemoryDatabase
    :param owner: Account owning the project.
    :type owner: Actor
    :returns: The project and its pages in book order.
    :rtype: tuple[Project, list[Page]]
    """
    project = make_project(owner_id=owner.account_id)
    keys = FractionalOrderKeys().spread(lower=None, upper=None, count=len(LAYOUT))
    pages = [
        evolve(
            make_page(project_id=project.id, order_key=key),
            kind=kind,
            included=included,
            label=STALE_LABEL,
            label_manual=True,
        )
        for key, (kind, included) in zip(keys, LAYOUT, strict=True)
    ]
    await commit_project(database, project, *pages)
    return project, pages


def _labels(database: InMemoryDatabase, pages: list[Page]) -> list[str]:
    """Read the committed labels of pages.

    :param database: In-memory database to read.
    :type database: InMemoryDatabase
    :param pages: Pages to read the labels of.
    :type pages: list[Page]
    :returns: The label of each page as committed, in the order given.
    :rtype: list[str]
    """
    return [database.tables.pages[page.id].label for page in pages]


def _written(database: InMemoryDatabase, before: dict[PageId, Page]) -> set[PageId]:
    """Find the pages whose rows were written since ``before``, which the frozen rows tell by their identity.

    :param database: In-memory database to compare.
    :type database: InMemoryDatabase
    :param before: The committed page rows as they were.
    :type before: dict[PageId, Page]
    :returns: Identifiers of the pages whose row is another object now.
    :rtype: set[PageId]
    """
    return {page_id for page_id, page in database.tables.pages.items() if before[page_id] is not page}


class TestUpdate:
    """Tests for PageService.update()."""

    async def test_changes_the_four_fields_and_writes_one_row(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_actor: Actor,
        fx_clock: FixedClock,
        fx_events: RecordingEventBus,
    ) -> None:
        """Verify the number, the kind, the inclusion and the notes change together, stamped, announced once.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_clock: Clock the service stamps pages with.
        :type fx_clock: FixedClock
        :param fx_events: Recording event bus of the test.
        :type fx_events: RecordingEventBus
        """
        project, pages = await _commit_book(fx_database, fx_actor)
        before = dict(fx_database.tables.pages)
        fx_clock.moment = LATER

        updated = await fx_service().update(
            fx_actor,
            project.id,
            pages[2].id,
            PageChanges(label='[4]', kind=PageKind.OTHER, included=False, notes='Library stamp'),
        )

        stored = fx_database.tables.pages[pages[2].id]
        expect(
            (stored.label, stored.kind, stored.included, stored.notes)
            == ('[4]', PageKind.OTHER, False, 'Library stamp')
        )
        expect(stored.updated_at == LATER)
        expect((stored.id, stored.order_key, stored.scan_id) == (pages[2].id, pages[2].order_key, pages[2].scan_id))
        expect(updated.page == stored)
        expect(updated.position == 2)
        expect(_written(fx_database, before) == {pages[2].id})
        expect(
            fx_events.published
            == [PagesChanged(project_id=project.id, page_ids=[pages[2].id], change=PageChange.EDITED)]
        )
        assert_expectations()

    async def test_a_field_left_as_none_keeps_its_value(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify changing the label alone leaves the kind, the inclusion and the notes of the page as they were.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project, pages = await _commit_book(fx_database, fx_actor)

        await fx_service().update(fx_actor, project.id, pages[3].id, PageChanges(label=''))

        stored = fx_database.tables.pages[pages[3].id]
        assert evolve(stored, label=STALE_LABEL, label_manual=True, updated_at=EPOCH, revision=0) == pages[3]
        assert (stored.label, stored.label_manual) == ('', False)

    async def test_missing_page_page_of_another_project_and_foreign_project_are_not_found(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify a page that does not exist, one of another project, and a project of another account are all 404.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project, pages = await _commit_book(fx_database, fx_actor)
        stranger, strangers_pages = await _commit_book(fx_database, evolve(fx_actor, account_id=new_account_id()))
        other = make_page(project_id=project.id, order_key='z0')

        with pytest.raises(NotFoundError):
            await fx_service().update(fx_actor, project.id, other.id, PageChanges(label='1'))
        with pytest.raises(NotFoundError):
            await fx_service().update(fx_actor, project.id, strangers_pages[0].id, PageChanges(label='1'))
        with pytest.raises(NotFoundError):
            await fx_service().update(fx_actor, stranger.id, strangers_pages[0].id, PageChanges(label='1'))
        assert _labels(fx_database, pages) == [STALE_LABEL] * len(pages)


class TestConcurrentWrites:
    """Tests for the use cases that write pages while another request commits a change to one of them."""

    @staticmethod
    def _racing_service(
        database: InMemoryDatabase,
        runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue],
        assets: LocalAssetStore,
        monkeypatch: pytest.MonkeyPatch,
        rival: Callable[[], Awaitable[None]],
    ) -> PageService:
        """Build the page service over a unit of work whose page reads are followed by the rival's commit.

        The unit of work builds new repositories when it rolls back, so the rival races the first attempt only.

        :param database: In-memory database shared with the rival.
        :type database: InMemoryDatabase
        :param runtime: The recording bus, the clock and the queue the service reports through.
        :type runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue]
        :param assets: Asset store of the test.
        :type assets: LocalAssetStore
        :param monkeypatch: Fixture that restores the patched repository after the test.
        :type monkeypatch: pytest.MonkeyPatch
        :param rival: Request committing a change to a page, run after each read of one page.
        :type rival: Callable[[], Awaitable[None]]
        :returns: The service of one request.
        :rtype: PageService
        """
        uow = InMemoryUnitOfWork(database)
        read = uow.pages.get

        async def get_then_race(page_id: PageId) -> Page:
            """Read a page, then let the rival commit a change of it.

            :param page_id: Identifier of the page.
            :type page_id: PageId
            :returns: The page as it was before the rival wrote.
            :rtype: Page
            """
            page = await read(page_id)
            await rival()
            return page

        monkeypatch.setattr(uow.pages, 'get', get_then_race)
        return make_page_service(uow, assets, runtime)

    async def test_an_edit_of_another_field_made_between_the_read_and_the_write_is_kept(
        self,
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue],
        fx_actor: Actor,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify the kind the service sets is applied to the page the rival has just labelled, so both edits stay.

        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_runtime: The recording bus, the clock and the queue the service reports through.
        :type fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue]
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param monkeypatch: Fixture that restores the patched repository after the test.
        :type monkeypatch: pytest.MonkeyPatch
        """
        project, pages = await _commit_book(fx_database, fx_actor)
        writes = 0

        async def rival() -> None:
            """Commit a new label of the page the service reads, once."""
            nonlocal writes
            if writes:
                return
            writes += 1
            uow = InMemoryUnitOfWork(fx_database)
            await uow.pages.update(evolve(await uow.pages.get(pages[2].id), label=RIVAL_LABEL))
            await uow.commit()

        updated = await self._racing_service(fx_database, fx_runtime, fx_asset_store, monkeypatch, rival).update(
            fx_actor, project.id, pages[2].id, PageChanges(kind=PageKind.OTHER)
        )

        stored = fx_database.tables.pages[pages[2].id]
        expect((stored.label, stored.kind) == (RIVAL_LABEL, PageKind.OTHER))
        expect(updated.page == stored)
        expect(stored.revision == 2)
        assert_expectations()
