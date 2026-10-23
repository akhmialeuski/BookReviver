"""Tests for the use cases that put the pages of a book in order."""

from typing import TYPE_CHECKING, NamedTuple, override

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.domain.enums import BookResource, Side
from bookreviver.domain.errors import AnchorInsideMovedPagesError, ConflictError, NotFoundError
from bookreviver.domain.values import PageAnchor, SliceRequest
from bookreviver.ports.ordering import OrderKeys
from tests.helpers.builders import (
    EPOCH,
    make_page,
    make_record,
    make_scan,
    make_source,
    new_account_id,
    open_for,
)
from tests.helpers.page_services import make_page_service
from tests.helpers.seeding import ensure_account, store_book

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.adapters.storage import LocalAssetStore
    from bookreviver.domain.entities import Actor, BookRecord, Page, Source
    from bookreviver.domain.ids import BookId, PageId
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.services.pages import PageService
    from tests.helpers.clock import FixedClock
    from tests.helpers.database import UnitOfWorkFactory
    from tests.helpers.fake_board import FakeJobBoard
    from tests.helpers.fakes_jobs import RecordingEventBus
    from tests.helpers.page_writes import PageWrites

pytestmark = pytest.mark.anyio

FIRST_SOURCE_PAGES: int = 3
SECOND_SOURCE_PAGES: int = 2
LATER: datetime = EPOCH.replace(year=EPOCH.year + 1)
CASE_ARG: str = 'case'


class Book(NamedTuple):
    """A book of two sources whose pages stand one after the other in the order of their upload.

    :ivar book_record: The record of the book.
    :ivar first: The source of the first three pages.
    :ivar second: The source of the last two pages.
    :ivar pages: The five pages in book order.
    """

    book_record: BookRecord
    first: Source
    second: Source
    pages: list[Page]


class MoveCase(NamedTuple):
    """One move of the last page of the five-page book next to the first.

    :ivar side: Side of the first page the last page is put on.
    :ivar expected: Indexes of the original pages in the book order that follows.
    """

    side: Side
    expected: list[int]


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


async def _commit_book(uow: UnitOfWork, owner: Actor) -> Book:
    """Commit a book of five pages, three from one source and two from another, in the order of upload.

    :param uow: Unit of work to commit through, over a database that stores the account of the owner.
    :type uow: UnitOfWork
    :param owner: Account owning the book.
    :type owner: Actor
    :returns: The stored book.
    :rtype: Book
    """
    book_record = make_record()
    first, second = (
        make_source(name='part1.pdf'),
        make_source(name='part2.pdf'),
    )
    scans = [
        *(make_scan(source=first, number=number) for number in range(FIRST_SOURCE_PAGES)),
        *(make_scan(source=second, number=number) for number in range(SECOND_SOURCE_PAGES)),
    ]
    keys = FractionalOrderKeys().spread(lower=None, upper=None, count=len(scans))
    pages = [make_page(order_key=key, scan=scan) for key, scan in zip(keys, scans, strict=True)]
    await store_book(uow, book_record, *pages, owner=owner.account_id, sources=[first, second], scans=scans)
    return Book(book_record=book_record, first=first, second=second, pages=pages)


async def _book_order(service: PageService, actor: Actor, book_id: BookId) -> list[PageId]:
    """Read the identifiers of a book's pages in book order.

    :param service: Page service to read through.
    :type service: PageService
    :param actor: Account owning the book.
    :type actor: Actor
    :param book_id: Identifier of the book.
    :type book_id: BookId
    :returns: The identifiers of every page, first page first.
    :rtype: list[PageId]
    """
    manifest = await service.manifest(open_for(actor, book_id), SliceRequest(limit=100))
    return [overview.page.id for overview in manifest.items]


class TestMove:
    """Tests for PageService.move()."""

    @pytest.mark.parametrize(
        CASE_ARG,
        [
            MoveCase(side=Side.BEFORE, expected=[4, 0, 1, 2, 3]),
            MoveCase(side=Side.AFTER, expected=[0, 4, 1, 2, 3]),
        ],
        ids=['before-the-first-page', 'after-the-first-page'],
    )
    async def test_puts_the_page_next_to_the_anchor_and_writes_one_row(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_page_writes: PageWrites,
        fx_actor: Actor,
        case: MoveCase,
    ) -> None:
        """Verify moving the last page changes its place and only its row.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_page_writes: Record of the pages the page repository writes.
        :type fx_page_writes: PageWrites
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param case: Side of the first page the last page is put on, and the book order that follows.
        :type case: MoveCase
        """
        side, expected = case
        book = await _commit_book(fx_uow(), fx_actor)
        fx_page_writes.start()

        moved = await fx_service().move(
            open_for(fx_actor, book.book_record.id), book.pages[4].id, PageAnchor(page_id=book.pages[0].id, side=side)
        )

        expect(
            await _book_order(fx_service(), fx_actor, book.book_record.id)
            == [book.pages[index].id for index in expected]
        )
        expect(fx_page_writes.page_ids == {book.pages[4].id})
        expect(moved.position == expected.index(4))
        assert_expectations()

    async def test_keeps_the_identity_of_the_moved_page_and_stamps_its_update_time(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        fx_clock: FixedClock,
    ) -> None:
        """Verify a move changes only the key and the update time of a page, never its identifier or its scan.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_clock: Clock the service stamps pages with.
        :type fx_clock: FixedClock
        """
        book = await _commit_book(fx_uow(), fx_actor)
        fx_clock.moment = LATER

        await fx_service().move(
            open_for(fx_actor, book.book_record.id),
            book.pages[2].id,
            PageAnchor(page_id=book.pages[0].id, side=Side.BEFORE),
        )

        stored = await fx_uow().read_book(book.book_record.id, lambda repos: repos.pages.get(book.pages[2].id))
        expect(stored.updated_at == LATER)
        expect(evolve(stored, order_key=book.pages[2].order_key, updated_at=EPOCH) == book.pages[2])
        expect(stored.order_key < book.pages[0].order_key)
        assert_expectations()

    async def test_is_one_change_of_the_pages_after_the_commit(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        fx_book_changes: RecordingEventBus,
    ) -> None:
        """Verify a move is one change of the book, which names its pages.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_book_changes: Where the units of work of the test announce the changes of the books.
        :type fx_book_changes: RecordingEventBus
        """
        book = await _commit_book(fx_uow(), fx_actor)

        fx_book_changes.published.clear()
        await fx_service().move(
            open_for(fx_actor, book.book_record.id),
            book.pages[3].id,
            PageAnchor(page_id=book.pages[0].id, side=Side.AFTER),
        )

        assert [BookResource.PAGES in parts for parts in fx_book_changes.changed_parts()] == [True]

    async def test_anchor_that_is_the_page_itself_conflicts(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_page_writes: PageWrites,
        fx_actor: Actor,
        fx_book_changes: RecordingEventBus,
    ) -> None:
        """Verify a page cannot be put next to itself, which leaves the book and the events as they were.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_page_writes: Record of the pages the page repository writes.
        :type fx_page_writes: PageWrites
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_book_changes: Where the units of work of the test announce the changes of the books.
        :type fx_book_changes: RecordingEventBus
        """
        book = await _commit_book(fx_uow(), fx_actor)
        fx_page_writes.start()

        fx_book_changes.published.clear()
        with pytest.raises(AnchorInsideMovedPagesError):
            await fx_service().move(
                open_for(fx_actor, book.book_record.id),
                book.pages[1].id,
                PageAnchor(page_id=book.pages[1].id, side=Side.AFTER),
            )

        expect(fx_page_writes.page_ids == set())
        expect(fx_book_changes.published == [])
        assert_expectations()

    async def test_key_another_page_holds_conflicts_and_publishes_nothing(
        self,
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        fx_asset_store: LocalAssetStore,
        fx_runtime: tuple[RecordingEventBus, FixedClock, FakeJobBoard],
    ) -> None:
        """Verify a move that would give a page the key of another page is refused, as a lost race is.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_asset_store: Local asset store over the test's storage root.
        :type fx_asset_store: LocalAssetStore
        :param fx_runtime: The recording bus, the stopped clock and the job board of the test.
        :type fx_runtime: tuple[RecordingEventBus, FixedClock, FakeJobBoard]
        """
        book = await _commit_book(fx_uow(), fx_actor)
        service = make_page_service(fx_uow(), fx_asset_store, fx_runtime, order_keys=TakenKey(book.pages[2].order_key))

        with pytest.raises(ConflictError):
            await service.move(
                open_for(fx_actor, book.book_record.id),
                book.pages[4].id,
                PageAnchor(page_id=book.pages[0].id, side=Side.AFTER),
            )

        assert fx_runtime[0].published == []

    async def test_missing_page_and_anchor_are_not_found(
        self,
        fx_service: Callable[[], PageService],
        fx_database: SqlDatabase,
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
    ) -> None:
        """Verify a page and an anchor that are not pages of the book both answer not found.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: Database of the test.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        book = await _commit_book(fx_uow(), fx_actor)
        owner = evolve(fx_actor, account_id=new_account_id())
        await ensure_account(fx_database, owner.account_id)
        stranger = await _commit_book(fx_uow(), owner)
        known = PageAnchor(page_id=book.pages[0].id, side=Side.BEFORE)
        missing = PageAnchor(page_id=stranger.pages[0].id, side=Side.BEFORE)

        with pytest.raises(NotFoundError):
            await fx_service().move(open_for(fx_actor, book.book_record.id), stranger.pages[1].id, known)
        with pytest.raises(NotFoundError):
            await fx_service().move(open_for(fx_actor, book.book_record.id), book.pages[1].id, missing)


class TestMoveGroup:
    """Tests for PageService.move_group()."""

    @pytest.mark.parametrize(
        CASE_ARG,
        [
            GroupCase(chosen=[3, 1], anchor_index=4, side=Side.AFTER, expected=[0, 2, 4, 1, 3]),
            GroupCase(chosen=[2, 3], anchor_index=0, side=Side.BEFORE, expected=[2, 3, 0, 1, 4]),
            GroupCase(chosen=[0, 1], anchor_index=3, side=Side.BEFORE, expected=[2, 0, 1, 3, 4]),
        ],
        ids=['to-the-end', 'to-the-start', 'between-pages'],
    )
    async def test_puts_the_group_in_a_run_in_book_order_and_writes_only_its_rows(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_page_writes: PageWrites,
        fx_actor: Actor,
        case: GroupCase,
    ) -> None:
        """Verify the group stands together at the place, in the order it had, and no other row is written.

        The chosen indexes are given out of book order, and the group still keeps its book order.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_page_writes: Record of the pages the page repository writes.
        :type fx_page_writes: PageWrites
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param case: Pages to move, the page and side they go to, and the book order that follows.
        :type case: GroupCase
        """
        chosen, anchor_index, side, expected = case
        book = await _commit_book(fx_uow(), fx_actor)
        fx_page_writes.start()

        await fx_service().move_group(
            open_for(fx_actor, book.book_record.id),
            [book.pages[index].id for index in chosen],
            PageAnchor(page_id=book.pages[anchor_index].id, side=side),
        )

        expect(
            await _book_order(fx_service(), fx_actor, book.book_record.id)
            == [book.pages[index].id for index in expected]
        )
        expect(fx_page_writes.page_ids == {book.pages[index].id for index in chosen})
        assert_expectations()

    async def test_is_one_change_of_the_pages_however_large_the_group(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        fx_book_changes: RecordingEventBus,
    ) -> None:
        """Verify moving several pages is one change of the book, however many pages the group has.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_book_changes: Where the units of work of the test announce the changes of the books.
        :type fx_book_changes: RecordingEventBus
        """
        book = await _commit_book(fx_uow(), fx_actor)

        fx_book_changes.published.clear()
        await fx_service().move_group(
            open_for(fx_actor, book.book_record.id),
            [book.pages[3].id, book.pages[1].id],
            PageAnchor(page_id=book.pages[4].id, side=Side.AFTER),
        )

        assert [BookResource.PAGES in parts for parts in fx_book_changes.changed_parts()] == [True]

    async def test_anchor_inside_the_group_conflicts(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_page_writes: PageWrites,
        fx_actor: Actor,
        fx_book_changes: RecordingEventBus,
    ) -> None:
        """Verify a group cannot be put next to one of its own pages, which changes nothing.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_page_writes: Record of the pages the page repository writes.
        :type fx_page_writes: PageWrites
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_book_changes: Where the units of work of the test announce the changes of the books.
        :type fx_book_changes: RecordingEventBus
        """
        book = await _commit_book(fx_uow(), fx_actor)
        fx_page_writes.start()

        fx_book_changes.published.clear()
        with pytest.raises(AnchorInsideMovedPagesError):
            await fx_service().move_group(
                open_for(fx_actor, book.book_record.id),
                [book.pages[1].id, book.pages[2].id],
                PageAnchor(page_id=book.pages[2].id, side=Side.BEFORE),
            )

        expect(fx_page_writes.page_ids == set())
        expect(fx_book_changes.published == [])
        assert_expectations()

    async def test_page_of_another_book_is_not_found(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_page_writes: PageWrites,
        fx_actor: Actor,
    ) -> None:
        """Verify a group naming a page of another book is refused whole, without moving the others.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_page_writes: Record of the pages the page repository writes.
        :type fx_page_writes: PageWrites
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        book = await _commit_book(fx_uow(), fx_actor)
        other = await _commit_book(fx_uow(), fx_actor)
        fx_page_writes.start()

        with pytest.raises(NotFoundError):
            await fx_service().move_group(
                open_for(fx_actor, book.book_record.id),
                [book.pages[1].id, other.pages[0].id],
                PageAnchor(page_id=book.pages[4].id, side=Side.AFTER),
            )

        assert fx_page_writes.page_ids == set()


class TestMoveSource:
    """Tests for PageService.move_source()."""

    async def test_puts_every_page_of_the_source_in_a_run_after_the_anchor(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_page_writes: PageWrites,
        fx_actor: Actor,
        fx_book_changes: RecordingEventBus,
    ) -> None:
        """Verify the pages of the first source stand together after the last page, in one change and their rows only.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_page_writes: Record of the pages the page repository writes.
        :type fx_page_writes: PageWrites
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_book_changes: Where the units of work of the test announce the changes of the books.
        :type fx_book_changes: RecordingEventBus
        """
        book = await _commit_book(fx_uow(), fx_actor)
        fx_page_writes.start()

        fx_book_changes.published.clear()
        await fx_service().move_source(
            open_for(fx_actor, book.book_record.id),
            book.first.id,
            PageAnchor(page_id=book.pages[4].id, side=Side.AFTER),
        )

        group = [book.pages[index].id for index in range(FIRST_SOURCE_PAGES)]
        expect(
            await _book_order(fx_service(), fx_actor, book.book_record.id) == [*(p.id for p in book.pages[3:]), *group]
        )
        expect(fx_page_writes.page_ids == set(group))
        expect([BookResource.PAGES in parts for parts in fx_book_changes.changed_parts()] == [True])
        assert_expectations()

    async def test_source_without_pages_moves_nothing_and_publishes_nothing(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        fx_book_changes: RecordingEventBus,
    ) -> None:
        """Verify a source whose pages were all deleted or never made is no error and no event.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_book_changes: Where the units of work of the test announce the changes of the books.
        :type fx_book_changes: RecordingEventBus
        """
        book = await _commit_book(fx_uow(), fx_actor)
        bare = make_source(name='bare.pdf')
        await fx_uow().change_book(book.book_record.id, lambda repos, _book_record: repos.sources.add(bare))

        fx_book_changes.published.clear()
        await fx_service().move_source(
            open_for(fx_actor, book.book_record.id), bare.id, PageAnchor(page_id=book.pages[0].id, side=Side.BEFORE)
        )

        assert fx_book_changes.published == []

    async def test_anchor_among_the_pages_of_the_source_conflicts(
        self, fx_service: Callable[[], PageService], fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify the source cannot be put next to one of its own pages.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        book = await _commit_book(fx_uow(), fx_actor)

        with pytest.raises(AnchorInsideMovedPagesError):
            await fx_service().move_source(
                open_for(fx_actor, book.book_record.id),
                book.first.id,
                PageAnchor(page_id=book.pages[1].id, side=Side.AFTER),
            )

    async def test_source_of_another_book_is_not_found(
        self, fx_service: Callable[[], PageService], fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify a source is found only through the book that holds it.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        book = await _commit_book(fx_uow(), fx_actor)
        other = await _commit_book(fx_uow(), fx_actor)

        with pytest.raises(NotFoundError):
            await fx_service().move_source(
                open_for(fx_actor, book.book_record.id),
                other.first.id,
                PageAnchor(page_id=book.pages[0].id, side=Side.BEFORE),
            )
