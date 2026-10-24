"""Tests for the page use cases."""

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import JobKind, Stage, VersionState
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import PageId
from bookreviver.domain.values import Renditions, SliceRequest
from tests.helpers.builders import (
    make_page,
    make_page_stage,
    make_page_version,
    make_record,
    make_scan,
    make_source,
    new_book_id,
)
from tests.helpers.seeding import commit_record, store_book

if TYPE_CHECKING:
    from collections.abc import Callable

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import Actor, BookRecord, Page, PageVersion
    from bookreviver.domain.ids import AccountId, BookId
    from bookreviver.ports.persistence import BookDatabases, BookRepositories, UnitOfWork
    from bookreviver.services.pages import PageService
    from tests.helpers.builders import BookOpener
    from tests.helpers.database import UnitOfWorkFactory
    from tests.helpers.fake_board import FakeJobBoard

pytestmark = pytest.mark.anyio

# Stored against the book order, and sorting as a0, a0V, a1 in bytes
STORED_KEYS: list[str] = ['a1', 'a0', 'a0V']
BOOK_ORDER: list[int] = [1, 2, 0]
WINDOW: SliceRequest = SliceRequest(offset=1, limit=1)
FILE_CONTENT: bytes = b'derived file'


def _ready(version: PageVersion) -> PageVersion:
    """Make a version that has its files written.

    :param version: A pending version.
    :type version: PageVersion
    :returns: The version as ready, with its renditions ready.
    :rtype: PageVersion
    """
    return evolve(version, renditions=Renditions(ready=True), state=VersionState.READY)


async def _commit_book(
    database: SqlDatabase, uow: UnitOfWork, owner: AccountId, *, databases: BookDatabases
) -> tuple[BookId, list[Page], list[PageVersion]]:
    """Commit a book of three pages stored against the book order, the middle one in the book a placeholder.

    :param database: Catalogue of the test, which stores the owner when it does not already.
    :type database: SqlDatabase
    :param uow: Unit of work over the catalogue and the books of the test.
    :type uow: UnitOfWork
    :param owner: Account owning the book.
    :type owner: AccountId
    :param databases: Opens the databases of the books of the test.
    :type databases: BookDatabases
    :returns: The identifier of the book, the pages in the order they were stored, and the base versions of the two
              that have one.
    :rtype: tuple[BookId, list[Page], list[PageVersion]]
    """
    source = make_source()
    scans = [make_scan(source=source, number=number) for number in range(2)]
    shown = [scans[0], None, scans[1]]
    pages = [make_page(order_key=key, scan=scan) for key, scan in zip(STORED_KEYS, shown, strict=True)]
    versions = [make_page_version(page_id=pages[index].id) for index in (0, 2)]
    book_id = await commit_record(
        database,
        uow,
        make_record(),
        *pages,
        owner=owner,
        sources=[source],
        scans=scans,
        versions=versions,
        databases=databases,
    )
    return book_id, pages, versions


class TestManifest:
    """Tests for PageService.manifest()."""

    async def test_lists_the_pages_in_book_order_with_their_positions_and_versions(
        self,
        fx_service: Callable[[], PageService],
        fx_database: SqlDatabase,
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify pages come in the byte order of their keys, numbered from zero, each with its own base version.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: Database of the test.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        book_id, pages, versions = await _commit_book(
            fx_database, fx_uow(), fx_actor.account_id, databases=fx_open_book.databases
        )

        manifest = await fx_service().manifest(fx_open_book(fx_actor, book_id), SliceRequest())

        expect([overview.page for overview in manifest.items] == [pages[index] for index in BOOK_ORDER])
        expect([overview.position for overview in manifest.items] == [0, 1, 2])
        expect([overview.image_version for overview in manifest.items] == [None, versions[1], versions[0]])
        expect(manifest.total == len(pages))
        assert_expectations()

    async def test_a_window_continues_the_positions_of_the_book(
        self,
        fx_service: Callable[[], PageService],
        fx_database: SqlDatabase,
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify a window starting at an offset numbers its pages from that offset and reports the whole book.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: Database of the test.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        book_id, pages, _ = await _commit_book(
            fx_database, fx_uow(), fx_actor.account_id, databases=fx_open_book.databases
        )

        manifest = await fx_service().manifest(fx_open_book(fx_actor, book_id), WINDOW)

        expect([(overview.page, overview.position) for overview in manifest.items] == [(pages[2], 1)])
        expect(manifest.total == len(pages))
        assert_expectations()

    async def test_shows_the_newest_base_version_of_a_page(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify a page cut from its scan again shows the base version created last.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        page = make_page()
        old, new = make_page_version(page_id=page.id, minutes=1), make_page_version(page_id=page.id, minutes=2)
        book_id = await store_book(
            fx_uow(),
            make_record(),
            page,
            owner=fx_actor.account_id,
            versions=[new, old],
            databases=fx_open_book.databases,
        )

        manifest = await fx_service().manifest(fx_open_book(fx_actor, book_id), SliceRequest())

        assert [overview.image_version for overview in manifest.items] == [new]

    async def test_shows_the_current_version_of_the_latest_stage_that_has_an_image(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify the page shows its deskewed version, and not the base version it was made from or the regions without an image.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        page = make_page()
        base = _ready(make_page_version(page_id=page.id, minutes=1))
        deskewed = evolve(_ready(make_page_version(page_id=page.id, minutes=2)), stage=Stage.GEOMETRY, input_id=base.id)
        regions = evolve(make_page_version(page_id=page.id, minutes=3), stage=Stage.LAYOUT, input_id=deskewed.id)
        book_id = await store_book(
            fx_uow(),
            make_record(),
            page,
            owner=fx_actor.account_id,
            versions=[base, deskewed, regions],
            databases=fx_open_book.databases,
        )

        def save_heads(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Save the records that name the base version and the deskewed one as current, and the regions too.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it, which the work does not need.
            :type _book_record: BookRecord
            """
            for version in (base, deskewed):
                repos.page_stages.save(
                    make_page_stage(page_id=page.id, stage=version.stage, head_version_id=version.id)
                )
            repos.page_stages.save(make_page_stage(page_id=page.id, stage=Stage.LAYOUT, head_version_id=regions.id))

        await fx_open_book.databases.open(book_id).change(save_heads)

        manifest = await fx_service().manifest(fx_open_book(fx_actor, book_id), SliceRequest())

        assert [overview.image_version for overview in manifest.items] == [deskewed]

    async def test_shows_the_current_version_even_when_a_newer_base_version_exists(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify a version the user chose as current is shown over a base version that was made after it.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        page = make_page()
        chosen, newer = (_ready(make_page_version(page_id=page.id, minutes=minutes)) for minutes in (1, 2))
        book_id = await store_book(
            fx_uow(),
            make_record(),
            page,
            owner=fx_actor.account_id,
            versions=[chosen, newer],
            databases=fx_open_book.databases,
        )
        await fx_open_book.databases.open(book_id).change(
            lambda repos, _book_record: repos.page_stages.save(
                make_page_stage(page_id=page.id, stage=Stage.PAGE_SPLIT, head_version_id=chosen.id)
            ),
        )

        manifest = await fx_service().manifest(fx_open_book(fx_actor, book_id), SliceRequest())

        assert [overview.image_version for overview in manifest.items] == [chosen]

    async def test_included_only_leaves_out_the_pages_kept_out_and_numbers_the_rest(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify a listing of the included pages skips an excluded page, and numbers the listed pages among themselves.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        pages = [make_page(order_key=key) for key in ('a0', 'a1', 'a2')]
        pages[1] = evolve(pages[1], included=False)
        book_id = await store_book(
            fx_uow(), make_record(), *pages, owner=fx_actor.account_id, databases=fx_open_book.databases
        )

        manifest = await fx_service().manifest(fx_open_book(fx_actor, book_id), SliceRequest(), included_only=True)

        expect([overview.page for overview in manifest.items] == [pages[0], pages[2]])
        expect([overview.position for overview in manifest.items] == [0, 1])
        expect(manifest.total == len(manifest.items))
        assert_expectations()

    async def test_names_the_source_of_a_page_with_a_scan_and_none_for_a_placeholder(
        self,
        fx_service: Callable[[], PageService],
        fx_database: SqlDatabase,
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify each page of the window knows the source of its scan, which selects the pages of one source.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: Database of the test.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        book_id, _, _ = await _commit_book(fx_database, fx_uow(), fx_actor.account_id, databases=fx_open_book.databases)
        [source, *_] = await fx_open_book.databases.open(book_id).read(lambda repos: repos.sources.list_all())

        manifest = await fx_service().manifest(fx_open_book(fx_actor, book_id), SliceRequest())

        assert [overview.source_id for overview in manifest.items] == [None, source.id, source.id]


class TestGet:
    """Tests for PageService.get()."""

    async def test_returns_the_page_with_its_position_and_base_version(
        self,
        fx_service: Callable[[], PageService],
        fx_database: SqlDatabase,
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify a page read by its identifier knows its place in the book and its base version.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: Database of the test.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        book_id, pages, versions = await _commit_book(
            fx_database, fx_uow(), fx_actor.account_id, databases=fx_open_book.databases
        )

        overview = await fx_service().get(fx_open_book(fx_actor, book_id), pages[0].id)

        expect(overview.page == pages[0])
        expect(overview.position == len(pages) - 1)
        expect(overview.image_version == versions[0])
        assert_expectations()

    async def test_page_of_another_book_of_the_actor_is_not_found(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify a page is found only through the book that holds it, even for an owner of both books.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        page = make_page()
        book_id = await store_book(fx_uow(), make_record(), owner=fx_actor.account_id, databases=fx_open_book.databases)
        await store_book(fx_uow(), make_record(), page, owner=fx_actor.account_id, databases=fx_open_book.databases)

        with pytest.raises(NotFoundError):
            await fx_service().get(fx_open_book(fx_actor, book_id), page.id)

    async def test_missing_page_is_not_found(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify an identifier no page has is reported as not found.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        book_id = await store_book(fx_uow(), make_record(), owner=fx_actor.account_id, databases=fx_open_book.databases)

        with pytest.raises(NotFoundError):
            await fx_service().get(fx_open_book(fx_actor, book_id), PageId(uuid4()))


class TestJobsQueuedTogether:
    """Tests for the check of the active jobs and the put of a job, which are one step of the event loop."""

    @pytest.mark.parametrize('requests', [2, 5], ids=['two', 'five'])
    async def test_requests_asking_for_the_detection_together_queue_one_job(
        self, requests: int, fx_service: Callable[[], PageService], fx_board: FakeJobBoard
    ) -> None:
        """Verify the requests for the detection of one book put one job on the board and the others find it active.

        Each request builds its own service, and none of them awaits between the check of the board and the put.

        :param requests: How many requests ask for the detection together.
        :type requests: int
        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_board: Board that records the jobs put on it.
        :type fx_board: FakeJobBoard
        """
        book_id = new_book_id()

        for _ in range(requests):
            fx_service().detect_new_pages(book_id)

        assert [job.kind for job in fx_board.put_jobs if job.book_id == book_id] == [JobKind.DETECT_CONTENT]
