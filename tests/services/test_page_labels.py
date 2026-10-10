"""Tests for the use cases that edit the fields of a page, on in-memory persistence."""

from typing import TYPE_CHECKING

import anyio
import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.domain.changes import PageChanges
from bookreviver.domain.enums import ContentType, PageChange, PageKind, Side
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.events import PagesChanged
from bookreviver.domain.values import PageAnchor
from bookreviver.services.projects import book_pages
from tests.helpers.book_gate import hold_book
from tests.helpers.builders import EPOCH, make_page, make_project, new_account_id
from tests.helpers.page_services import make_page_service
from tests.helpers.seeding import commit_project

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from datetime import datetime
    from typing import Any

    from bookreviver.adapters.clock.system import FixedClock
    from bookreviver.adapters.jobs.recording import RecordingJobQueue
    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.adapters.storage import LocalAssetStore
    from bookreviver.domain.entities import Actor, Page, Project
    from bookreviver.domain.ids import PageId, ProjectId
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.services.pages import PageService
    from tests.helpers.fakes_jobs import RecordingEventBus

pytestmark = pytest.mark.anyio

LATER: datetime = EPOCH.replace(year=EPOCH.year + 1)
STALE_LABEL: str = 'old'
NEW_LABEL: str = '[4]'
NEW_NOTES: str = 'Library stamp'
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


async def _update_kind(service: PageService, actor: Actor, project_id: ProjectId, page_ids: list[PageId]) -> None:
    """Change the kind of the third page, as a request to edit a page does.

    :param service: Page service of the request.
    :type service: PageService
    :param actor: Account the service acts for.
    :type actor: Actor
    :param project_id: Identifier of the project.
    :type project_id: ProjectId
    :param page_ids: Identifiers of the pages of the book in book order.
    :type page_ids: list[PageId]
    """
    await service.update(actor, project_id, page_ids[2], PageChanges(kind=PageKind.OTHER))


async def _delete_page(service: PageService, actor: Actor, project_id: ProjectId, page_ids: list[PageId]) -> None:
    """Delete the third page, as a request to delete a page does.

    :param service: Page service of the request.
    :type service: PageService
    :param actor: Account the service acts for.
    :type actor: Actor
    :param project_id: Identifier of the project.
    :type project_id: ProjectId
    :param page_ids: Identifiers of the pages of the book in book order.
    :type page_ids: list[PageId]
    """
    await service.delete(actor, project_id, page_ids[2])


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
            PageChanges(label=NEW_LABEL, kind=PageKind.OTHER, included=False, notes=NEW_NOTES),
        )

        stored = fx_database.tables.pages[pages[2].id]
        expect(
            (stored.label, stored.kind, stored.included, stored.notes) == (NEW_LABEL, PageKind.OTHER, False, NEW_NOTES)
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
        assert evolve(stored, label=STALE_LABEL, label_manual=True, updated_at=EPOCH) == pages[3]
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
    """Tests for the use cases that write pages while another block of the same book is open."""

    async def test_a_job_writing_a_moved_page_during_a_group_move_keeps_both_changes(
        self,
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue],
        fx_actor: Actor,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify a group move ends in the new order while a job records the content of one of the moved pages.

        It replays the failure of ``pages.spec.ts`` of 2026-10-09. The move is inside its block when the job's write
        starts as a concurrent task, so the job's block has to wait for the move and then read the new order. Ordering
        is proved with events and ``wait_all_tasks_blocked``, never by waiting for a time.

        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_runtime: The recording bus, the clock and the queue the service reports through.
        :type fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue]
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param monkeypatch: Fixture that restores the patched method after the test.
        :type monkeypatch: pytest.MonkeyPatch
        """
        project, pages = await _commit_book(fx_database, fx_actor)
        moved = [pages[0].id, pages[1].id]
        anchor = PageAnchor(page_id=pages[3].id, side=Side.AFTER)
        mover = InMemoryUnitOfWork(fx_database)
        gate = hold_book(mover, monkeypatch)
        seen_by_job: list[PageId] = []

        async def job_writes_content() -> None:
            """Record the content of the second moved page in a block of its own, and note the order it read there."""
            job = InMemoryUnitOfWork(fx_database)
            async with job.change_book(project.id):
                seen_by_job.extend(page.id for page in await book_pages(job.pages, project.id))
                await job.pages.update(evolve(await job.pages.get(moved[1]), content_type=ContentType.COLOR_PICTURE))

        async with anyio.create_task_group() as group:
            group.start_soon(
                make_page_service(mover, fx_asset_store, fx_runtime).move_group, fx_actor, project.id, moved, anchor
            )
            await gate.entered.wait()
            group.start_soon(job_writes_content)
            await anyio.wait_all_tasks_blocked()
            expect(not seen_by_job)
            gate.proceed.set()

        order = sorted(fx_database.tables.pages.values(), key=lambda page: page.order_key)
        expected = [pages[2].id, pages[3].id, *moved, pages[4].id]
        expect([page.id for page in order][:5] == expected)
        expect(fx_database.tables.pages[moved[1]].content_type == ContentType.COLOR_PICTURE)
        expect(seen_by_job[:5] == expected)
        assert_expectations()

    @pytest.mark.parametrize('write', [_update_kind, _delete_page], ids=['update', 'delete'])
    async def test_a_use_case_started_while_another_is_inside_its_block_waits_for_it(
        self,
        write: Callable[[PageService, Actor, ProjectId, list[PageId]], Coroutine[Any, Any, None]],
        fx_database: InMemoryDatabase,
        fx_service_over: Callable[[UnitOfWork], PageService],
        fx_actor: Actor,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify a use case that starts while a move holds the book writes nothing before the move has ended.

        :param write: The use case that starts second, given the service, the actor, the project and the page ids.
        :type write: Callable[[PageService, Actor, ProjectId, list[PageId]], Coroutine[Any, Any, None]]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_service_over: Function building the page service over a unit of work.
        :type fx_service_over: Callable[[UnitOfWork], PageService]
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param monkeypatch: Fixture that restores the patched method after the test.
        :type monkeypatch: pytest.MonkeyPatch
        """
        project, pages = await _commit_book(fx_database, fx_actor)
        ids = [page.id for page in pages]
        mover = InMemoryUnitOfWork(fx_database)
        gate = hold_book(mover, monkeypatch)
        before = dict(fx_database.tables.pages)

        async with anyio.create_task_group() as group:
            group.start_soon(
                fx_service_over(mover).move,
                fx_actor,
                project.id,
                ids[0],
                PageAnchor(page_id=ids[3], side=Side.AFTER),
            )
            await gate.entered.wait()
            group.start_soon(
                write,
                fx_service_over(InMemoryUnitOfWork(fx_database)),
                fx_actor,
                project.id,
                ids,
            )
            await anyio.wait_all_tasks_blocked()
            expect(_written(fx_database, before) == set())
            gate.proceed.set()

        expect(fx_database.tables.pages[ids[0]].order_key != pages[0].order_key)
        assert_expectations()
