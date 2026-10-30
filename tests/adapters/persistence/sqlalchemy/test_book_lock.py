"""Test of a move of pages and a write of one of them by a job that race on one SQLite file through two changes.

The move is inside its ``Book.change`` when the job asks for its own, so the job's change has to wait for the move to
commit, and then reads the order the move wrote. Neither write is lost, which no revision of a row had to tell.
"""

import threading
from functools import partial
from typing import TYPE_CHECKING

import anyio
import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.pages import ContentType
from bookreviver.domain.slices import SliceRequest
from tests.helpers.book_hold import HOLD_LIMIT_SECONDS
from tests.helpers.builders import make_page

if TYPE_CHECKING:
    from collections.abc import Callable

    from bookreviver.domain.books import BookRecord
    from bookreviver.domain.ids import BookId, PageId
    from bookreviver.ports.persistence import BookRepositories
    from tests.helpers.database import LocatedBookDatabases
    from tests.helpers.tracers import AskingTracer

pytestmark = pytest.mark.anyio

ORDER_KEYS: tuple[str, ...] = ('a0', 'a1', 'a2', 'a3', 'a4')
# The keys the two moved pages get, which sort between the fourth page, 'a3', and the fifth, 'a4'
MOVED_KEYS: tuple[str, str] = ('a3V', 'a3W')
MOVED: int = 2
ANCHOR: int = 3


class TestMoveAgainstJobWrite:
    """Tests for a change that moves pages while a job writes one of them, on SQLite."""

    async def test_a_job_write_asked_inside_a_group_move_waits_and_both_writes_are_kept(
        self, fx_traced_databases: Callable[..., LocatedBookDatabases], fx_book: BookId, fx_tracer: AskingTracer
    ) -> None:
        """Verify the job's change waits for the move, reads the new order, and writes the content type over it.

        The move holds the book by blocking its thread after it has read the window. The job asks for its own change
        then, which the test knows from the start of its span, and is not allowed to read before the move has
        committed, which is checked after ``wait_all_tasks_blocked``.

        :param fx_traced_databases: Function opening the databases of the traced books.
        :type fx_traced_databases: Callable[..., LocatedBookDatabases]
        :param fx_book: Committed book of the database, which has no page yet.
        :type fx_book: BookId
        :param fx_tracer: Tracer of the test, which tells when the job has asked for its change.
        :type fx_tracer: AskingTracer
        """
        database = fx_traced_databases().open(fx_book)
        pages = [make_page(order_key=key) for key in ORDER_KEYS]
        await database.change(lambda repos, _book_record: repos.pages.add_many(pages))
        moved = [pages[0].id, pages[1].id]
        entered, proceed = anyio.Event(), threading.Event()
        seen_by_job: list[PageId] = []

        def move_the_group(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Read the window, hold the book until the test lets the move go, and move the first two pages.

            :param repos: BookRepositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, whose pages are listed.
            :type _book_record: BookRecord
            """
            window = repos.pages.list_window(SliceRequest())
            anyio.from_thread.run_sync(entered.set)
            proceed.wait(HOLD_LIMIT_SECONDS)
            by_id = {page.id: page for page in window.items}
            for page_id, key in zip(moved, MOVED_KEYS, strict=True):
                repos.pages.update(evolve(by_id[page_id], order_key=key))

        def job_writes_content(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Write the content type of the second moved page after the order the move left.

            :param repos: BookRepositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, whose pages are listed.
            :type _book_record: BookRecord
            """
            window = repos.pages.list_window(SliceRequest())
            seen_by_job.extend(page.id for page in window.items)
            page = repos.pages.get(moved[1])
            repos.pages.update(evolve(page, content_type=ContentType.COLOR_PICTURE))

        async with anyio.create_task_group() as group:
            group.start_soon(partial(database.change, move_the_group))
            await entered.wait()
            job_asking = fx_tracer.ask_after(1)
            group.start_soon(partial(database.change, job_writes_content))
            await job_asking.wait()
            await anyio.wait_all_tasks_blocked()
            expect(not seen_by_job)
            proceed.set()

        expected = [pages[MOVED].id, pages[ANCHOR].id, *moved, pages[4].id]
        stored = (await database.read(lambda repos: repos.pages.list_window(SliceRequest()))).items
        expect([page.id for page in stored] == expected)
        expect(next(page for page in stored if page.id == moved[1]).content_type == ContentType.COLOR_PICTURE)
        expect(seen_by_job == expected)
        assert_expectations()
