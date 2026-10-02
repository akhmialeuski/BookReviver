"""Tests for the use cases that put the pages of a book in order, against in-memory persistence."""

from typing import TYPE_CHECKING, NamedTuple, override

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.domain.enums import PageChange, Side
from bookreviver.domain.errors import AnchorInsideMovedPagesError, ConflictError, NotFoundError
from bookreviver.domain.events import PagesChanged
from bookreviver.domain.values import PageAnchor, SliceRequest
from bookreviver.ports.ordering import OrderKeys
from tests.helpers.builders import (
    EPOCH,
    make_page,
    make_project,
    make_scan,
    make_source,
    new_account_id,
)
from tests.helpers.page_services import make_page_service
from tests.helpers.seeding import commit_project

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime

    from bookreviver.adapters.clock.system import FixedClock
    from bookreviver.adapters.jobs.recording import RecordingJobQueue
    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.adapters.storage import LocalAssetStore
    from bookreviver.domain.entities import Actor, Page, Project, Source
    from bookreviver.domain.ids import PageId, ProjectId
    from bookreviver.services.pages import PageService
    from tests.helpers.fakes_jobs import RecordingEventBus

pytestmark = pytest.mark.anyio

FIRST_SOURCE_PAGES: int = 3
SECOND_SOURCE_PAGES: int = 2
LATER: datetime = EPOCH.replace(year=EPOCH.year + 1)


class Book(NamedTuple):
    """A book of two sources whose pages stand one after the other in the order of their upload.

    :ivar project: The project of the book.
    :ivar first: The source of the first three pages.
    :ivar second: The source of the last two pages.
    :ivar pages: The five pages in book order.
    """

    project: Project
    first: Source
    second: Source
    pages: list[Page]


class GroupCase(NamedTuple):
    """One move of a group of pages of the five-page book.

    :ivar chosen: Indexes of the pages to move, in the order the request names them.
    :ivar anchor_index: Index of the page the group is put next to.
    :ivar side: Side of the anchor the group is put on.
    :ivar expected: Indexes of the original pages in the book order that follows.
    """

    chosen: list[int]
    anchor_index: int
    side: Side
    expected: list[int]


class TakenKey(OrderKeys):
    """Order keys that always answer one key, which a page of the book may hold already.

    :ivar key: The key every call answers.
    """

    def __init__(self, key: str) -> None:
        """Answer ``key`` to every call.

        :param key: The key to answer.
        :type key: str
        """
        self.key = key

    @override
    def between(self, *, lower: str | None, upper: str | None) -> str:
        """Return the one key.

        :param lower: Ignored.
        :type lower: str | None
        :param upper: Ignored.
        :type upper: str | None
        :returns: The key given at construction.
        :rtype: str
        """
        return self.key

    @override
    def spread(self, *, lower: str | None, upper: str | None, count: int) -> Sequence[str]:
        """Return the one key for every page.

        :param lower: Ignored.
        :type lower: str | None
        :param upper: Ignored.
        :type upper: str | None
        :param count: Number of keys.
        :type count: int
        :returns: The key given at construction, ``count`` times.
        :rtype: Sequence[str]
        """
        return [self.key] * count


async def _commit_book(database: InMemoryDatabase, owner: Actor) -> Book:
    """Commit a book of five pages, three from one source and two from another, in the order of upload.

    :param database: In-memory database to commit into.
    :type database: InMemoryDatabase
    :param owner: Account owning the project.
    :type owner: Actor
    :returns: The stored book.
    :rtype: Book
    """
    project = make_project(owner_id=owner.account_id)
    first, second = (
        make_source(project_id=project.id, name='part1.pdf'),
        make_source(project_id=project.id, name='part2.pdf'),
    )
    scans = [
        *(make_scan(source=first, number=number) for number in range(FIRST_SOURCE_PAGES)),
        *(make_scan(source=second, number=number) for number in range(SECOND_SOURCE_PAGES)),
    ]
    keys = FractionalOrderKeys().spread(lower=None, upper=None, count=len(scans))
    pages = [make_page(project_id=project.id, order_key=key, scan=scan) for key, scan in zip(keys, scans, strict=True)]
    await commit_project(database, project, *pages, sources=[first, second], scans=scans)
    return Book(project=project, first=first, second=second, pages=pages)


async def _book_order(service: PageService, actor: Actor, project_id: ProjectId) -> list[PageId]:
    """Read the identifiers of a book's pages in book order.

    :param service: Page service to read through.
    :type service: PageService
    :param actor: Account owning the project.
    :type actor: Actor
    :param project_id: Identifier of the project.
    :type project_id: ProjectId
    :returns: The identifiers of every page, first page first.
    :rtype: list[PageId]
    """
    manifest = await service.manifest(actor, project_id, SliceRequest(limit=100))
    return [overview.page.id for overview in manifest.items]


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


class TestMove:
    """Tests for PageService.move()."""

    @pytest.mark.parametrize(
        ('side', 'expected'),
        [(Side.BEFORE, [4, 0, 1, 2, 3]), (Side.AFTER, [0, 4, 1, 2, 3])],
        ids=['before-the-first-page', 'after-the-first-page'],
    )
    async def test_puts_the_page_next_to_the_anchor_and_writes_one_row(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_actor: Actor,
        side: Side,
        expected: list[int],
    ) -> None:
        """Verify moving the last page changes its place and only its row.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param side: Side of the first page the last page is put on.
        :type side: Side
        :param expected: Indexes of the original pages in the book order that follows.
        :type expected: list[int]
        """
        book = await _commit_book(fx_database, fx_actor)
        before = dict(fx_database.tables.pages)

        moved = await fx_service().move(
            fx_actor, book.project.id, book.pages[4].id, PageAnchor(page_id=book.pages[0].id, side=side)
        )

        expect(
            await _book_order(fx_service(), fx_actor, book.project.id) == [book.pages[index].id for index in expected]
        )
        expect(_written(fx_database, before) == {book.pages[4].id})
        expect(moved.position == expected.index(4))
        assert_expectations()

    async def test_keeps_the_identity_of_the_moved_page_and_stamps_its_update_time(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_actor: Actor,
        fx_clock: FixedClock,
    ) -> None:
        """Verify a move changes only the key and the update time of a page, never its identifier or its scan.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_clock: Clock the service stamps pages with.
        :type fx_clock: FixedClock
        """
        book = await _commit_book(fx_database, fx_actor)
        fx_clock.moment = LATER

        await fx_service().move(
            fx_actor, book.project.id, book.pages[2].id, PageAnchor(page_id=book.pages[0].id, side=Side.BEFORE)
        )

        stored = fx_database.tables.pages[book.pages[2].id]
        expect(stored.updated_at == LATER)
        expect(evolve(stored, order_key=book.pages[2].order_key, updated_at=EPOCH, revision=0) == book.pages[2])
        expect(stored.order_key < book.pages[0].order_key)
        assert_expectations()

    async def test_publishes_one_event_after_the_commit(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_actor: Actor,
        fx_events: RecordingEventBus,
    ) -> None:
        """Verify a move announces the page it moved as a move of the project.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_events: Recording event bus of the test.
        :type fx_events: RecordingEventBus
        """
        book = await _commit_book(fx_database, fx_actor)

        await fx_service().move(
            fx_actor, book.project.id, book.pages[3].id, PageAnchor(page_id=book.pages[0].id, side=Side.AFTER)
        )

        assert fx_events.published == [
            PagesChanged(project_id=book.project.id, page_ids=[book.pages[3].id], change=PageChange.MOVED)
        ]

    async def test_anchor_that_is_the_page_itself_conflicts(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_actor: Actor,
        fx_events: RecordingEventBus,
    ) -> None:
        """Verify a page cannot be put next to itself, which leaves the book and the events as they were.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_events: Recording event bus of the test.
        :type fx_events: RecordingEventBus
        """
        book = await _commit_book(fx_database, fx_actor)
        before = dict(fx_database.tables.pages)

        with pytest.raises(AnchorInsideMovedPagesError):
            await fx_service().move(
                fx_actor, book.project.id, book.pages[1].id, PageAnchor(page_id=book.pages[1].id, side=Side.AFTER)
            )

        expect(_written(fx_database, before) == set())
        expect(fx_events.published == [])
        assert_expectations()

    async def test_key_another_page_holds_conflicts_and_publishes_nothing(
        self,
        fx_database: InMemoryDatabase,
        fx_actor: Actor,
        fx_asset_store: LocalAssetStore,
        fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue],
    ) -> None:
        """Verify a move that would give a page the key of another page is refused, as a lost race is.

        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_asset_store: Local asset store over the test's storage root.
        :type fx_asset_store: LocalAssetStore
        :param fx_runtime: The recording bus, the stopped clock and the recording queue of the test.
        :type fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue]
        """
        book = await _commit_book(fx_database, fx_actor)
        service = make_page_service(
            InMemoryUnitOfWork(fx_database), fx_asset_store, fx_runtime, order_keys=TakenKey(book.pages[2].order_key)
        )

        with pytest.raises(ConflictError):
            await service.move(
                fx_actor, book.project.id, book.pages[4].id, PageAnchor(page_id=book.pages[0].id, side=Side.AFTER)
            )

        assert fx_runtime[0].published == []

    async def test_missing_page_anchor_and_foreign_project_are_not_found(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify an unknown page, an unknown anchor and another account's project all answer not found.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        book = await _commit_book(fx_database, fx_actor)
        stranger = await _commit_book(fx_database, evolve(fx_actor, account_id=new_account_id()))
        known = PageAnchor(page_id=book.pages[0].id, side=Side.BEFORE)
        missing = PageAnchor(page_id=stranger.pages[0].id, side=Side.BEFORE)

        with pytest.raises(NotFoundError):
            await fx_service().move(fx_actor, book.project.id, stranger.pages[1].id, known)
        with pytest.raises(NotFoundError):
            await fx_service().move(fx_actor, book.project.id, book.pages[1].id, missing)
        with pytest.raises(NotFoundError):
            await fx_service().move(fx_actor, stranger.project.id, stranger.pages[1].id, missing)


class TestMoveGroup:
    """Tests for PageService.move_group()."""

    @pytest.mark.parametrize(
        'case',
        [
            GroupCase(chosen=[3, 1], anchor_index=4, side=Side.AFTER, expected=[0, 2, 4, 1, 3]),
            GroupCase(chosen=[2, 3], anchor_index=0, side=Side.BEFORE, expected=[2, 3, 0, 1, 4]),
            GroupCase(chosen=[0, 1], anchor_index=3, side=Side.BEFORE, expected=[2, 0, 1, 3, 4]),
        ],
        ids=['to-the-end', 'to-the-start', 'between-pages'],
    )
    async def test_puts_the_group_in_a_run_in_book_order_and_writes_only_its_rows(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor, case: GroupCase
    ) -> None:
        """Verify the group stands together at the place, in the order it had, and no other row is written.

        The chosen indexes are given out of book order, and the group still keeps its book order.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param case: Pages to move, the page and side they go to, and the book order that follows.
        :type case: GroupCase
        """
        chosen, anchor_index, side, expected = case
        book = await _commit_book(fx_database, fx_actor)
        before = dict(fx_database.tables.pages)

        await fx_service().move_group(
            fx_actor,
            book.project.id,
            [book.pages[index].id for index in chosen],
            PageAnchor(page_id=book.pages[anchor_index].id, side=side),
        )

        expect(
            await _book_order(fx_service(), fx_actor, book.project.id) == [book.pages[index].id for index in expected]
        )
        expect(_written(fx_database, before) == {book.pages[index].id for index in chosen})
        assert_expectations()

    async def test_publishes_one_event_naming_the_group_in_book_order(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_actor: Actor,
        fx_events: RecordingEventBus,
    ) -> None:
        """Verify moving several pages publishes one event, however many pages the group has.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_events: Recording event bus of the test.
        :type fx_events: RecordingEventBus
        """
        book = await _commit_book(fx_database, fx_actor)

        await fx_service().move_group(
            fx_actor,
            book.project.id,
            [book.pages[3].id, book.pages[1].id],
            PageAnchor(page_id=book.pages[4].id, side=Side.AFTER),
        )

        assert fx_events.published == [
            PagesChanged(
                project_id=book.project.id, page_ids=[book.pages[1].id, book.pages[3].id], change=PageChange.MOVED
            )
        ]

    async def test_anchor_inside_the_group_conflicts(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_actor: Actor,
        fx_events: RecordingEventBus,
    ) -> None:
        """Verify a group cannot be put next to one of its own pages, which changes nothing.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_events: Recording event bus of the test.
        :type fx_events: RecordingEventBus
        """
        book = await _commit_book(fx_database, fx_actor)
        before = dict(fx_database.tables.pages)

        with pytest.raises(AnchorInsideMovedPagesError):
            await fx_service().move_group(
                fx_actor,
                book.project.id,
                [book.pages[1].id, book.pages[2].id],
                PageAnchor(page_id=book.pages[2].id, side=Side.BEFORE),
            )

        expect(_written(fx_database, before) == set())
        expect(fx_events.published == [])
        assert_expectations()

    async def test_page_of_another_project_is_not_found(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify a group naming a page of another project is refused whole, without moving the others.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        book = await _commit_book(fx_database, fx_actor)
        other = await _commit_book(fx_database, fx_actor)
        before = dict(fx_database.tables.pages)

        with pytest.raises(NotFoundError):
            await fx_service().move_group(
                fx_actor,
                book.project.id,
                [book.pages[1].id, other.pages[0].id],
                PageAnchor(page_id=book.pages[4].id, side=Side.AFTER),
            )

        assert _written(fx_database, before) == set()


class TestMoveSource:
    """Tests for PageService.move_source()."""

    async def test_puts_every_page_of_the_source_in_a_run_after_the_anchor(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_actor: Actor,
        fx_events: RecordingEventBus,
    ) -> None:
        """Verify the pages of the first source stand together after the last page, with one event and their rows only.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_events: Recording event bus of the test.
        :type fx_events: RecordingEventBus
        """
        book = await _commit_book(fx_database, fx_actor)
        before = dict(fx_database.tables.pages)

        await fx_service().move_source(
            fx_actor, book.project.id, book.first.id, PageAnchor(page_id=book.pages[4].id, side=Side.AFTER)
        )

        group = [book.pages[index].id for index in range(FIRST_SOURCE_PAGES)]
        expect(await _book_order(fx_service(), fx_actor, book.project.id) == [*(p.id for p in book.pages[3:]), *group])
        expect(_written(fx_database, before) == set(group))
        expect(
            fx_events.published == [PagesChanged(project_id=book.project.id, page_ids=group, change=PageChange.MOVED)]
        )
        assert_expectations()

    async def test_source_without_pages_moves_nothing_and_publishes_nothing(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_actor: Actor,
        fx_events: RecordingEventBus,
    ) -> None:
        """Verify a source whose pages were all deleted or never made is no error and no event.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_events: Recording event bus of the test.
        :type fx_events: RecordingEventBus
        """
        book = await _commit_book(fx_database, fx_actor)
        bare = make_source(project_id=book.project.id, name='bare.pdf')
        uow = InMemoryUnitOfWork(fx_database)
        await uow.sources.add(bare)
        await uow.commit()

        await fx_service().move_source(
            fx_actor, book.project.id, bare.id, PageAnchor(page_id=book.pages[0].id, side=Side.BEFORE)
        )

        assert fx_events.published == []

    async def test_anchor_among_the_pages_of_the_source_conflicts(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify the source cannot be put next to one of its own pages.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        book = await _commit_book(fx_database, fx_actor)

        with pytest.raises(AnchorInsideMovedPagesError):
            await fx_service().move_source(
                fx_actor, book.project.id, book.first.id, PageAnchor(page_id=book.pages[1].id, side=Side.AFTER)
            )

    async def test_source_of_another_project_is_not_found(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify a source is found only through the project that holds it.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        book = await _commit_book(fx_database, fx_actor)
        other = await _commit_book(fx_database, fx_actor)

        with pytest.raises(NotFoundError):
            await fx_service().move_source(
                fx_actor, book.project.id, other.first.id, PageAnchor(page_id=book.pages[0].id, side=Side.BEFORE)
            )
