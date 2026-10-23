"""Test of a move of pages and a write of one of them by a job that race on one SQLite file through two changes.

The move is inside its ``change_book`` when the job asks for its own, so the job's change has to wait for the move to
commit, and then reads the order the move wrote. Neither write is lost, which no revision of a row had to tell.
"""

import threading
from functools import partial
from typing import TYPE_CHECKING

import anyio
import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import ContentType
from bookreviver.domain.values import SliceRequest
from tests.helpers.book_hold import HOLD_LIMIT_SECONDS
from tests.helpers.builders import make_page

if TYPE_CHECKING:
    from collections.abc import Callable

    from bookreviver.domain.entities import Project
    from bookreviver.domain.ids import PageId
    from bookreviver.ports.persistence import BookRepositories, UnitOfWork
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
        self, fx_traced_uow: Callable[..., UnitOfWork], fx_project: Project, fx_tracer: AskingTracer
    ) -> None:
        """Verify the job's change waits for the move, reads the new order, and writes the content type over it.

        The move holds the book by blocking its thread after it has read the window. The job asks for its own change
        then, which the test knows from the start of its span, and is not allowed to read before the move has
        committed, which is checked after ``wait_all_tasks_blocked``.

        :param fx_traced_uow: Function opening a unit of work over the traced catalogue and books.
        :type fx_traced_uow: Callable[..., UnitOfWork]
        :param fx_project: Committed project of the database, whose book has no page yet.
        :type fx_project: Project
        :param fx_tracer: Tracer of the test, which tells when the job has asked for its change.
        :type fx_tracer: AskingTracer
        """
        uow = fx_traced_uow()
        pages = [make_page(project_id=fx_project.id, order_key=key) for key in ORDER_KEYS]
        await uow.change_book(fx_project.id, lambda repos, _project: repos.pages.add_many(pages))
        moved = [pages[0].id, pages[1].id]
        entered, proceed = anyio.Event(), threading.Event()
        seen_by_job: list[PageId] = []

        def move_the_group(repos: BookRepositories, project: Project) -> None:
            """Read the window, hold the book until the test lets the move go, and move the first two pages.

            :param repos: BookRepositories of the transaction.
            :type repos: BookRepositories
            :param project: Project of the book, whose pages are listed.
            :type project: Project
            """
            window = repos.pages.list_for_project(project.id, SliceRequest())
            anyio.from_thread.run_sync(entered.set)
            proceed.wait(HOLD_LIMIT_SECONDS)
            by_id = {page.id: page for page in window.items}
            for page_id, key in zip(moved, MOVED_KEYS, strict=True):
                repos.pages.update(evolve(by_id[page_id], order_key=key))

        def job_writes_content(repos: BookRepositories, project: Project) -> None:
            """Write the content type of the second moved page after the order the move left.

            :param repos: BookRepositories of the transaction.
            :type repos: BookRepositories
            :param project: Project of the book, whose pages are listed.
            :type project: Project
            """
            window = repos.pages.list_for_project(project.id, SliceRequest())
            seen_by_job.extend(page.id for page in window.items)
            page = repos.pages.get(moved[1])
            repos.pages.update(evolve(page, content_type=ContentType.COLOR_PICTURE))

        async with anyio.create_task_group() as group:
            group.start_soon(partial(uow.change_book, fx_project.id, move_the_group))
            await entered.wait()
            job_asking = fx_tracer.ask_after(1)
            group.start_soon(partial(uow.change_book, fx_project.id, job_writes_content))
            await job_asking.wait()
            await anyio.wait_all_tasks_blocked()
            expect(not seen_by_job)
            proceed.set()

        expected = [pages[MOVED].id, pages[ANCHOR].id, *moved, pages[4].id]
        stored = (
            await uow.read_book(
                fx_project.id, lambda repos: repos.pages.list_for_project(fx_project.id, SliceRequest())
            )
        ).items
        expect([page.id for page in stored] == expected)
        expect(next(page for page in stored if page.id == moved[1]).content_type == ContentType.COLOR_PICTURE)
        expect(seen_by_job == expected)
        assert_expectations()
