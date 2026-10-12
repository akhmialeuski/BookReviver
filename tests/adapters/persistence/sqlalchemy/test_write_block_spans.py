"""Tests for the ``write.block`` spans the engine of SqlDatabase makes around the blocks that hold SQLite's write lock.

The recording tracer is given to the database, so each block of a unit of work leaves one span with the kind of the
block, the book it held, the wait for the lock, the holding and how it ended. Two units of work over one file show the
wait. They are ordered by events and never by a sleep: the second block asks for the lock while the first holds it, and
only then is the first let go.
"""

from time import perf_counter
from typing import TYPE_CHECKING

import anyio
import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from bookreviver.domain.enums import BlockKind, BlockOutcome, SpanAttribute, SpanName
from bookreviver.domain.errors import BookBusyError
from tests.helpers.builders import make_page, make_project
from tests.helpers.schema import create_schema
from tests.helpers.seeding import commit_account, store_project
from tests.helpers.tracers import AskingTracer

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from bookreviver.adapters.tracing.recording import RecordingSpan
    from bookreviver.app.settings import Settings
    from bookreviver.domain.entities import Project

pytestmark = pytest.mark.anyio

# Long enough that a block which wrongly waits for a lock that is never given fails the test, and does not hang it
WAIT_SECONDS: float = 10.0
# Short enough that a block refused the lock gives up at once
IMPATIENT_SECONDS: float = 0.05
MILLISECONDS_PER_SECOND: int = 1_000
FAILED_BLOCK: str = 'The block failed after it wrote a page.'


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


@pytest.fixture
def fx_tracer() -> AskingTracer:
    """Build the tracer of the test, which every database of the test is given.

    :returns: A tracer with no span recorded.
    :rtype: AskingTracer
    """
    return AskingTracer()


@pytest.fixture
async def fx_traced_database(fx_settings: Settings, fx_tracer: AskingTracer) -> AsyncIterator[SqlDatabase]:
    """Open a database over a fresh file, whose blocks are timed by the tracer of the test.

    :param fx_settings: Settings with a fresh data directory of the test.
    :type fx_settings: Settings
    :param fx_tracer: Tracer of the test.
    :type fx_tracer: AskingTracer
    :returns: Iterator yielding the database and disposing of its engine afterwards.
    :rtype: AsyncIterator[SqlDatabase]
    """
    await create_schema(fx_settings)
    database = SqlDatabase(fx_settings.resolved_database_url, tracer=fx_tracer, wait_seconds=WAIT_SECONDS)
    yield database
    await database.dispose()


@pytest.fixture
async def fx_impatient_database(fx_traced_database: SqlDatabase, fx_tracer: AskingTracer) -> AsyncIterator[SqlDatabase]:
    """Open a second database over the file of the first, whose blocks give up for the lock almost at once.

    :param fx_traced_database: The database whose file and schema the second one shares.
    :type fx_traced_database: SqlDatabase
    :param fx_tracer: Tracer of the test.
    :type fx_tracer: AskingTracer
    :returns: Iterator yielding the database and disposing of its engine afterwards.
    :rtype: AsyncIterator[SqlDatabase]
    """
    url = fx_traced_database.engine.url.render_as_string(hide_password=False)
    database = SqlDatabase(url, tracer=fx_tracer, wait_seconds=IMPATIENT_SECONDS)
    yield database
    await database.dispose()


@pytest.fixture
async def fx_project(fx_traced_database: SqlDatabase, fx_tracer: AskingTracer) -> Project:
    """Commit an account and a project of it, which a ``change_book`` block needs to find.

    The blocks that store the project are spans too, which are not what a test looks at, so they are forgotten.

    :param fx_traced_database: Database to store into.
    :type fx_traced_database: SqlDatabase
    :param fx_tracer: Tracer of the test.
    :type fx_tracer: AskingTracer
    :returns: The project.
    :rtype: Project
    """
    project = make_project(owner_id=await commit_account(fx_traced_database))
    async with fx_traced_database.sessions() as session:
        await store_project(SqlAlchemyUnitOfWork(session), project)
    fx_tracer.finished.clear()
    return project


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

    async def test_block_refused_the_lock_ends_its_span_as_not_begun_with_its_wait(
        self,
        fx_traced_database: SqlDatabase,
        fx_impatient_database: SqlDatabase,
        fx_project: Project,
        fx_tracer: AskingTracer,
    ) -> None:
        """Verify a block that gives up for the lock ends its span with the time it waited, and held nothing.

        :param fx_traced_database: Database whose block holds the lock.
        :type fx_traced_database: SqlDatabase
        :param fx_impatient_database: Database over the same file, whose blocks give up after a few milliseconds.
        :type fx_impatient_database: SqlDatabase
        :param fx_project: Committed project of the database.
        :type fx_project: Project
        :param fx_tracer: Tracer of the test.
        :type fx_tracer: AskingTracer
        """
        async with (
            fx_traced_database.sessions() as holder,
            fx_impatient_database.sessions() as asker,
            SqlAlchemyUnitOfWork(holder).change_book(fx_project.id),
        ):
            with pytest.raises(BookBusyError):
                async with SqlAlchemyUnitOfWork(asker).change_book(fx_project.id):
                    pass
        refused, held = fx_tracer.named(SpanName.WRITE_BLOCK)
        expect(refused.attributes[SpanAttribute.BLOCK_OUTCOME] is BlockOutcome.NOT_BEGUN)
        expect(milliseconds(refused, SpanAttribute.WAIT_MS) > 0)
        expect(SpanAttribute.HOLD_MS not in refused.attributes)
        expect(held.attributes[SpanAttribute.BLOCK_OUTCOME] is BlockOutcome.COMMITTED)
        assert_expectations()
