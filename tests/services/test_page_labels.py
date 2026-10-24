"""Tests for the use cases that edit the fields of a page."""

from typing import TYPE_CHECKING

import anyio
import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.domain.changes import PageChanges
from bookreviver.domain.enums import ContentType, PageKind, Side
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.values import PageAnchor
from bookreviver.services.books import book_pages
from tests.helpers.book_hold import hold_book_in_thread
from tests.helpers.builders import EPOCH, make_page, make_record, new_account_id
from tests.helpers.seeding import ensure_account, store_book

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from typing import Any

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import Actor, BookRecord, Page
    from bookreviver.domain.ids import BookId, PageId
    from bookreviver.ports.persistence import BookRepositories, CatalogueDatabase
    from bookreviver.services.pages import PageService
    from tests.helpers.builders import BookOpener
    from tests.helpers.database import CatalogueDatabaseFactory, LocatedBookDatabases
    from tests.helpers.page_writes import PageWrites

pytestmark = pytest.mark.anyio

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


async def _commit_book(
    catalogue: CatalogueDatabase, owner: Actor, *, databases: LocatedBookDatabases
) -> tuple[BookId, list[Page]]:
    """Commit a book of seven pages of the kinds in ``LAYOUT``, each labelled by hand with a stale number.

    :param catalogue: Catalogue database to commit through, over a database that stores the account of the owner.
    :type catalogue: CatalogueDatabase
    :param owner: Account owning the book.
    :type owner: Actor
    :param databases: Opens the databases of the books of the test.
    :type databases: LocatedBookDatabases
    :returns: The book and its pages in book order.
    :rtype: tuple[BookRecord, list[Page]]
    """
    keys = FractionalOrderKeys().spread(lower=None, upper=None, count=len(LAYOUT))
    pages = [
        evolve(
            make_page(order_key=key),
            kind=kind,
            included=included,
            manual_label=STALE_LABEL,
        )
        for key, (kind, included) in zip(keys, LAYOUT, strict=True)
    ]
    book_id = await store_book(catalogue, make_record(), *pages, owner=owner.account_id, databases=databases)
    return book_id, pages


async def _stored(book_id: BookId, *, databases: LocatedBookDatabases) -> dict[PageId, Page]:
    """Read the committed pages of a book.

    :param book_id: Identifier of the book.
    :type book_id: BookId
    :param databases: Opens the databases of the books of the test.
    :type databases: LocatedBookDatabases
    :returns: The pages of the book as committed, in book order, by identifier.
    :rtype: dict[PageId, Page]
    """
    return await databases.open(book_id).read(lambda repos: {page.id: page for page in book_pages(repos.pages)})


async def _update_kind(
    service: PageService, actor: Actor, book_id: BookId, page_ids: list[PageId], opener: BookOpener
) -> None:
    """Change the kind of the third page, as a request to edit a page does.

    :param service: Page service of the request.
    :type service: PageService
    :param actor: Account the service acts for.
    :type actor: Actor
    :param book_id: Identifier of the book.
    :type book_id: BookId
    :param page_ids: Identifiers of the pages of the book in book order.
    :type page_ids: list[PageId]
    :param opener: Opens the books of the test with their databases and their files.
    :type opener: BookOpener
    """
    await service.update(opener(actor, book_id), page_ids[2], PageChanges(kind=PageKind.OTHER))


async def _delete_page(
    service: PageService, actor: Actor, book_id: BookId, page_ids: list[PageId], opener: BookOpener
) -> None:
    """Delete the third page, as a request to delete a page does.

    :param service: Page service of the request.
    :type service: PageService
    :param actor: Account the service acts for.
    :type actor: Actor
    :param book_id: Identifier of the book.
    :type book_id: BookId
    :param page_ids: Identifiers of the pages of the book in book order.
    :type page_ids: list[PageId]
    :param opener: Opens the books of the test with their databases and their files.
    :type opener: BookOpener
    """
    await service.delete(opener(actor, book_id), page_ids[2])


class TestUpdate:
    """Tests for PageService.update()."""

    async def test_an_update_writes_only_the_row_of_its_page(
        self,
        fx_service: Callable[[], PageService],
        fx_catalogue: CatalogueDatabaseFactory,
        fx_page_writes: PageWrites,
        fx_actor: Actor,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify an update of the third page writes its row and no row of another page.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_page_writes: Record of the pages the page repository writes.
        :type fx_page_writes: PageWrites
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        book_id, pages = await _commit_book(fx_catalogue(), fx_actor, databases=fx_open_book.databases)
        fx_page_writes.start()

        await fx_service().update(
            fx_open_book(fx_actor, book_id),
            pages[2].id,
            PageChanges(label=NEW_LABEL, kind=PageKind.OTHER, included=False, notes=NEW_NOTES),
        )

        assert fx_page_writes.page_ids == {pages[2].id}

    async def test_a_field_left_as_none_keeps_its_value(
        self,
        fx_service: Callable[[], PageService],
        fx_catalogue: CatalogueDatabaseFactory,
        fx_actor: Actor,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify changing the label alone leaves the kind, the inclusion and the notes of the page as they were.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        book_id, pages = await _commit_book(fx_catalogue(), fx_actor, databases=fx_open_book.databases)

        await fx_service().update(fx_open_book(fx_actor, book_id), pages[3].id, PageChanges(label=''))

        stored = (await _stored(book_id, databases=fx_open_book.databases))[pages[3].id]
        assert evolve(stored, manual_label=STALE_LABEL, updated_at=EPOCH) == pages[3]
        assert stored.manual_label is None

    async def test_missing_page_and_page_of_another_book_are_not_found(
        self,
        fx_service: Callable[[], PageService],
        fx_database: SqlDatabase,
        fx_catalogue: CatalogueDatabaseFactory,
        fx_actor: Actor,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify a page that does not exist and one of another book are both not found, and nothing changes.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: Database of the test.
        :type fx_database: SqlDatabase
        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        book_id, pages = await _commit_book(fx_catalogue(), fx_actor, databases=fx_open_book.databases)
        strangers_account = evolve(fx_actor, account_id=new_account_id())
        await ensure_account(fx_database, strangers_account.account_id)
        _, strangers_pages = await _commit_book(fx_catalogue(), strangers_account, databases=fx_open_book.databases)
        other = make_page(order_key='z0')

        with pytest.raises(NotFoundError):
            await fx_service().update(fx_open_book(fx_actor, book_id), other.id, PageChanges(label='1'))
        with pytest.raises(NotFoundError):
            await fx_service().update(fx_open_book(fx_actor, book_id), strangers_pages[0].id, PageChanges(label='1'))
        stored = await _stored(book_id, databases=fx_open_book.databases)
        assert [stored[page.id].manual_label for page in pages] == [STALE_LABEL] * len(pages)


class TestConcurrentWrites:
    """Tests for the use cases that write pages while another change of the same book is waiting for the book."""

    async def test_a_job_writing_a_moved_page_after_a_group_move_keeps_both_changes(
        self,
        fx_catalogue: CatalogueDatabaseFactory,
        fx_service: Callable[[], PageService],
        fx_actor: Actor,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify a group move ends in the new order while a job records the content of one of the moved pages.

        It replays the failure of ``pages.spec.ts`` of 2026-10-09. The book is held, the move asks for it first and
        the job's write second, so the job's change has to wait for the move and then read the new order. Ordering is
        proved with ``wait_all_tasks_blocked``, never by waiting for a time.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        book_id, pages = await _commit_book(fx_catalogue(), fx_actor, databases=fx_open_book.databases)
        moved = [pages[0].id, pages[1].id]
        anchor = PageAnchor(page_id=pages[3].id, side=Side.AFTER)
        seen_by_job: list[PageId] = []

        def job_writes_content(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Note the order of the book the job finds, and record the content of the second moved page.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it, which the work does not need.
            :type _book_record: BookRecord
            """
            seen_by_job.extend(page.id for page in book_pages(repos.pages))
            repos.pages.update(evolve(repos.pages.get(moved[1]), content_type=ContentType.COLOR_PICTURE))

        mover = fx_service()
        # The hold ends first, when the block ends, and the group then waits for the changes that queued behind it
        async with anyio.create_task_group() as group, hold_book_in_thread(fx_open_book.databases.open(book_id)):
            group.start_soon(mover.move_group, fx_open_book(fx_actor, book_id), moved, anchor)
            await anyio.wait_all_tasks_blocked()
            group.start_soon(fx_open_book.databases.open(book_id).change, job_writes_content)
            await anyio.wait_all_tasks_blocked()
            expect(not seen_by_job)

        stored = await _stored(book_id, databases=fx_open_book.databases)
        order = sorted(stored.values(), key=lambda page: page.order_key)
        expected = [pages[2].id, pages[3].id, *moved, pages[4].id]
        expect([page.id for page in order][:5] == expected)
        expect(stored[moved[1]].content_type == ContentType.COLOR_PICTURE)
        expect(seen_by_job[:5] == expected)
        assert_expectations()

    @pytest.mark.parametrize('write', [_update_kind, _delete_page], ids=['update', 'delete'])
    async def test_a_use_case_started_while_the_book_is_held_writes_after_the_one_asked_before_it(
        self,
        write: Callable[[PageService, Actor, BookId, list[PageId], BookOpener], Coroutine[Any, Any, None]],
        fx_page_writes: PageWrites,
        fx_service: Callable[[], PageService],
        fx_actor: Actor,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify two use cases that ask for a held book write nothing before it is free, and then in the order asked.

        The move asks first and the other use case second. Each is blocked at the book, so neither has written when
        the book is held, and after it is freed the move has written and the other use case ran on the book it left.

        :param write: The use case that starts second, given the service, the actor, the book and the page ids.
        :type write: Callable[[PageService, Actor, BookId, list[PageId], BookOpener], Coroutine[Any, Any, None]]
        :param fx_page_writes: Record of the pages the page repository writes.
        :type fx_page_writes: PageWrites
        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        book_id, pages = await _commit_book(fx_open_book.catalogue(), fx_actor, databases=fx_open_book.databases)
        ids = [page.id for page in pages]
        fx_page_writes.start()

        # The hold ends first, when the block ends, and the group then waits for the changes that queued behind it
        async with anyio.create_task_group() as group, hold_book_in_thread(fx_open_book.databases.open(book_id)):
            group.start_soon(
                fx_service().move,
                fx_open_book(fx_actor, book_id),
                ids[0],
                PageAnchor(page_id=ids[3], side=Side.AFTER),
            )
            await anyio.wait_all_tasks_blocked()
            group.start_soon(write, fx_service(), fx_actor, book_id, ids, fx_open_book)
            await anyio.wait_all_tasks_blocked()
            expect(fx_page_writes.page_ids == set())

        after = await _stored(book_id, databases=fx_open_book.databases)
        expect(after[ids[0]].order_key != pages[0].order_key)
        # The third page is the one both use cases of the parameters write: its row is changed or gone
        expect(after.get(ids[2]) != pages[2])
        assert_expectations()
