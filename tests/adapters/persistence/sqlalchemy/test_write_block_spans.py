"""Tests for the ``write.block`` spans the engine of SqlDatabase makes around the blocks that hold SQLite's write lock.

The recording tracer is given to the database, so each block of a unit of work leaves one span with the kind of the
block, the book it held, the wait for the lock, the holding and how it ended. Two units of work over one file show the
wait. They are ordered by events and never by a sleep: the second block asks for the lock while the first holds it, and
only then is the first let go. A block refused the lock ends its span as not begun, whether the queue of its process
or SQLite refused it.
"""

from enum import StrEnum
from time import perf_counter
from typing import TYPE_CHECKING

import anyio
import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.persistence.sqlalchemy.database import WRITE_QUEUE_INFO, WriteQueue
from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from bookreviver.domain.enums import BlockKind, BlockOutcome, SpanAttribute, SpanName
from bookreviver.domain.errors import BookBusyError
from tests.adapters.persistence.sqlalchemy.conftest import IMPATIENT_SECONDS
from tests.helpers.builders import make_page

if TYPE_CHECKING:
    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.adapters.tracing.recording import RecordingSpan
    from bookreviver.domain.entities import Project
    from tests.helpers.tracers import AskingTracer

pytestmark = pytest.mark.anyio

MILLISECONDS_PER_SECOND: int = 1_000
# The limit of the asking block that spends most of it in the queue, how long the queue is held, and the bound between
# one limit and two that the asker's error must come before: the middle of the two
ASKER_LIMIT_SECONDS: float = 1.0
QUEUE_HOLD_SECONDS: float = 0.7
WAIT_BOUND_SECONDS: float = 1.5
FAILED_BLOCK: str = 'The block failed after it wrote a page.'
REFUSER_ARG: str = 'refuser'


class Refuser(StrEnum):
    """What refuses a block the write lock, named as the test cases are."""

    SQLITE = 'refused-by-sqlite-busy-timeout'
    QUEUE = 'refused-by-the-queue-of-the-process'


def milliseconds(span: RecordingSpan, key: SpanAttribute) -> float:
    """Read a duration attribute of a span.

    :param span: An ended ``write.block`` span.
    :type span: RecordingSpan
    :param key: The attribute, which holds milliseconds.
    :type key: SpanAttribute
    :returns: The duration.
    :rtype: float
    """
    value = span.attributes[key]
    assert isinstance(value, float)
    return value


class TestWriteBlockSpan:
    """Tests for the span of one block of one unit of work."""

    async def test_change_book_block_makes_one_span_of_its_book_with_its_timings(
        self, fx_traced_database: SqlDatabase, fx_project: Project, fx_tracer: AskingTracer
    ) -> None:
        """Verify a committed ``change_book`` block gives one span with its kind, its book, three timings and its end.

        :param fx_traced_database: Database whose blocks are timed.
        :type fx_traced_database: SqlDatabase
        :param fx_project: Committed project of the database.
        :type fx_project: Project
        :param fx_tracer: Tracer of the test.
        :type fx_tracer: AskingTracer
        """
        async with fx_traced_database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            async with uow.change_book(fx_project.id):
                await uow.pages.add(make_page(project_id=fx_project.id))
        (span,) = fx_tracer.named(SpanName.WRITE_BLOCK)
        expect(span.attributes[SpanAttribute.BLOCK_KIND] is BlockKind.BOOK)
        expect(span.attributes[SpanAttribute.BOOK] == str(fx_project.id))
        expect(span.attributes[SpanAttribute.BLOCK_OUTCOME] is BlockOutcome.COMMITTED)
        for key in (SpanAttribute.WAIT_MS, SpanAttribute.HOLD_MS, SpanAttribute.COMMIT_MS):
            expect(milliseconds(span, key) >= 0, f'{key} is negative')
        assert_expectations()

    async def test_block_is_a_child_of_the_span_current_at_its_caller(
        self, fx_traced_database: SqlDatabase, fx_project: Project, fx_tracer: AskingTracer
    ) -> None:
        """Verify the ``write.block`` span, started in an engine listener, has the span of its caller as its parent.

        The listener runs in a greenlet, and the block gets the span of the caller as its parent only because
        SQLAlchemy copies the ``contextvars`` context of the caller into the greenlet. The report of a run attributes
        the wait for the lock to a step through this link, so it is checked through the real adapter.

        :param fx_traced_database: Database whose blocks are timed.
        :type fx_traced_database: SqlDatabase
        :param fx_project: Committed project of the database.
        :type fx_project: Project
        :param fx_tracer: Tracer of the test.
        :type fx_tracer: AskingTracer
        """
        with fx_tracer.span(SpanName.STEP):
            async with fx_traced_database.sessions() as session:
                uow = SqlAlchemyUnitOfWork(session)
                async with uow.change_book(fx_project.id):
                    await uow.pages.add(make_page(project_id=fx_project.id))
        (step,) = fx_tracer.named(SpanName.STEP)
        (block,) = fx_tracer.named(SpanName.WRITE_BLOCK)
        assert block.parent is step

    async def test_block_that_fails_makes_a_span_marked_rolled_back(
        self, fx_traced_database: SqlDatabase, fx_project: Project, fx_tracer: AskingTracer
    ) -> None:
        """Verify a block an exception leaves is rolled back, and its span says so and has no commit time.

        :param fx_traced_database: Database whose blocks are timed.
        :type fx_traced_database: SqlDatabase
        :param fx_project: Committed project of the database.
        :type fx_project: Project
        :param fx_tracer: Tracer of the test.
        :type fx_tracer: AskingTracer
        """

        async def write_then_fail() -> None:
            """Write a page in a block of the book, and fail before the block ends."""
            async with fx_traced_database.sessions() as session:
                uow = SqlAlchemyUnitOfWork(session)
                async with uow.change_book(fx_project.id):
                    await uow.pages.add(make_page(project_id=fx_project.id))
                    raise RuntimeError(FAILED_BLOCK)

        with pytest.raises(RuntimeError, match=FAILED_BLOCK):
            await write_then_fail()
        (span,) = fx_tracer.named(SpanName.WRITE_BLOCK)
        expect(span.attributes[SpanAttribute.BLOCK_OUTCOME] is BlockOutcome.ROLLED_BACK)
        expect(SpanAttribute.COMMIT_MS not in span.attributes)
        expect(milliseconds(span, SpanAttribute.WAIT_MS) >= 0)
        expect(milliseconds(span, SpanAttribute.HOLD_MS) >= 0)
        assert_expectations()

    async def test_change_block_is_of_the_other_kind_and_names_no_book(
        self, fx_traced_database: SqlDatabase, fx_tracer: AskingTracer
    ) -> None:
        """Verify a block outside the content of a book is marked as the other kind, with no book.

        :param fx_traced_database: Database whose blocks are timed.
        :type fx_traced_database: SqlDatabase
        :param fx_tracer: Tracer of the test.
        :type fx_tracer: AskingTracer
        """
        async with fx_traced_database.sessions() as session, SqlAlchemyUnitOfWork(session).change():
            pass
        (span,) = fx_tracer.named(SpanName.WRITE_BLOCK)
        expect(span.attributes[SpanAttribute.BLOCK_KIND] is BlockKind.OTHER)
        expect(SpanAttribute.BOOK not in span.attributes)
        assert_expectations()


class TestWaitForTheLock:
    """Tests for blocks of two units of work over one file, of which the second asks while the first holds the lock."""

    async def test_second_block_waits_at_least_as_long_as_the_first_held_the_lock_after_it_began_to_wait(
        self, fx_traced_database: SqlDatabase, fx_project: Project, fx_tracer: AskingTracer
    ) -> None:
        """Verify the wait of the second block covers what is left of the holding of the first, which ends later.

        The test notes the time when the second block is known to be asking, and the time just before the first is
        let go. The first cannot commit before the second asks, and the second cannot take the lock before the first
        commits, so the wait of the second is at least the time between the two notes.

        :param fx_traced_database: Database whose blocks are timed.
        :type fx_traced_database: SqlDatabase
        :param fx_project: Committed project of the database.
        :type fx_project: Project
        :param fx_tracer: Tracer of the test.
        :type fx_tracer: AskingTracer
        """
        holding, release = anyio.Event(), anyio.Event()
        second_asking = fx_tracer.ask_after(2)

        async def first() -> None:
            """Hold the book until the test lets it go."""
            async with (
                fx_traced_database.sessions() as session,
                SqlAlchemyUnitOfWork(session).change_book(fx_project.id),
            ):
                holding.set()
                await release.wait()

        async def second() -> None:
            """Ask for the book while the first block holds it, and take it once it is free."""
            async with (
                fx_traced_database.sessions() as session,
                SqlAlchemyUnitOfWork(session).change_book(fx_project.id),
            ):
                pass

        # Both are set inside the task group, which the type checker cannot know runs its body to the end
        asking_seen = released = perf_counter()
        async with anyio.create_task_group() as group:
            group.start_soon(first)
            await holding.wait()
            group.start_soon(second)
            await second_asking.wait()
            asking_seen = perf_counter()
            await anyio.wait_all_tasks_blocked()
            released = perf_counter()
            release.set()

        # The second block cannot end before the first, so the spans end in the order of the blocks
        held, waited = fx_tracer.named(SpanName.WRITE_BLOCK)
        still_held_ms = (released - asking_seen) * MILLISECONDS_PER_SECOND
        expect(milliseconds(waited, SpanAttribute.WAIT_MS) >= still_held_ms)
        expect(milliseconds(waited, SpanAttribute.WAIT_MS) > 0)
        expect(milliseconds(held, SpanAttribute.HOLD_MS) >= still_held_ms)
        assert_expectations()

    @pytest.mark.parametrize(REFUSER_ARG, list(Refuser), ids=list(Refuser))
    async def test_refused_block_ends_its_span_as_not_begun_with_its_wait(
        self,
        fx_traced_database: SqlDatabase,
        fx_impatient_database: SqlDatabase,
        fx_project: Project,
        fx_tracer: AskingTracer,
        refuser: Refuser,
    ) -> None:
        """Verify a block refused the lock ends its span with the time it waited, and held nothing.

        The asker's unit of work has a limit of a few milliseconds. When the holder belongs to the other database over
        the same file, each database has its own queue, as two processes would, so the asker meets the holder at the
        busy timeout of SQLite, which its block sets to what is left of that limit before it begins. When both blocks belong to the
        impatient database, the asker never reaches SQLite: the queue of the process refuses it.

        :param fx_traced_database: Database over the file, whose blocks wait long.
        :type fx_traced_database: SqlDatabase
        :param fx_impatient_database: Database over the same file, whose queue is its own, that the asker's unit of
                                      work gives up in after a few milliseconds.
        :type fx_impatient_database: SqlDatabase
        :param fx_project: Committed project of the database.
        :type fx_project: Project
        :param fx_tracer: Tracer of the test.
        :type fx_tracer: AskingTracer
        :param refuser: What refuses the asking block, which decides the database of the holder.
        :type refuser: Refuser
        """
        holding = fx_traced_database if refuser is Refuser.SQLITE else fx_impatient_database
        async with (
            holding.sessions() as holder,
            fx_impatient_database.sessions() as asker,
            SqlAlchemyUnitOfWork(holder).change_book(fx_project.id),
        ):
            with pytest.raises(BookBusyError):
                async with SqlAlchemyUnitOfWork(asker, wait_seconds=IMPATIENT_SECONDS).change_book(fx_project.id):
                    pass
        refused, held = fx_tracer.named(SpanName.WRITE_BLOCK)
        expect(refused.attributes[SpanAttribute.BLOCK_OUTCOME] is BlockOutcome.NOT_BEGUN)
        expect(milliseconds(refused, SpanAttribute.WAIT_MS) > 0)
        expect(SpanAttribute.HOLD_MS not in refused.attributes)
        expect(held.attributes[SpanAttribute.BLOCK_OUTCOME] is BlockOutcome.COMMITTED)
        assert_expectations()

    async def test_block_waits_for_the_queue_and_the_lock_together_never_twice_its_limit(
        self, fx_traced_database: SqlDatabase, fx_impatient_database: SqlDatabase, fx_project: Project
    ) -> None:
        """Verify a block that spent most of its limit in the queue leaves the write lock only what is left of it.

        A request must not wait twice its limit when another process writes: a block that stood in the queue for 0.7 s
        of a 1.0 s limit and then asked SQLite for the full limit would give up only after 1.7 s. The lock is held by
        a block of the other database, as a writer in another process would hold it, and the queue of the asker's
        database is held by a turn taken directly, so the asker spends most of its limit in the queue. Its error must
        come before the middle of one limit and two.

        :param fx_traced_database: Database over the file, whose block holds the write lock of SQLite.
        :type fx_traced_database: SqlDatabase
        :param fx_impatient_database: Database over the same file, with a queue of its own, that the asker opens its
                                      unit of work on.
        :type fx_impatient_database: SqlDatabase
        :param fx_project: Committed project of the database.
        :type fx_project: Project
        """
        sqlite_held, queue_held, release = anyio.Event(), anyio.Event(), anyio.Event()
        async with fx_impatient_database.sessions() as session:
            queue = session.info[WRITE_QUEUE_INFO]
        assert isinstance(queue, WriteQueue)

        async def hold_the_lock() -> None:
            """Hold the write lock of SQLite until the test lets it go."""
            async with (
                fx_traced_database.sessions() as session,
                SqlAlchemyUnitOfWork(session).change_book(fx_project.id),
            ):
                sqlite_held.set()
                await release.wait()

        async def hold_the_queue() -> None:
            """Hold the turn of the asker's queue for most of the asker's limit."""
            async with queue.turn(BlockKind.OTHER, None, wait_seconds=ASKER_LIMIT_SECONDS):
                queue_held.set()
                await anyio.sleep(QUEUE_HOLD_SECONDS)

        # Set inside the task group, which the type checker cannot know runs its body to the end
        waited_seconds = 0.0
        async with anyio.create_task_group() as group:
            group.start_soon(hold_the_lock)
            await sqlite_held.wait()
            group.start_soon(hold_the_queue)
            await queue_held.wait()
            asked_at = perf_counter()
            async with fx_impatient_database.sessions() as asker:
                with pytest.raises(BookBusyError):
                    async with SqlAlchemyUnitOfWork(asker, wait_seconds=ASKER_LIMIT_SECONDS).change():
                        pass
            waited_seconds = perf_counter() - asked_at
            release.set()
        assert waited_seconds < WAIT_BOUND_SECONDS
