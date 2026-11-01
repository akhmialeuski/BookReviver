"""Tests for the ``write.block`` spans SqlDatabase makes around the changes that hold the writer.

The recording tracer is given to the database, so each change of a catalogue database leaves one span with the kind of the
block, the book it held, the wait for the writer, the holding, the commit and how it ended. Two catalogue databases over one
file show the wait. They are ordered by events and never by a sleep: the second change asks for the writer while the
first holds it in a blocked thread, and only then is the first let go. A change refused the writer ends its span as not
begun, whether the queue of its process or SQLite refused it.
"""

import threading
from enum import StrEnum
from functools import partial
from time import perf_counter
from typing import TYPE_CHECKING

import anyio
import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.persistence.sqlalchemy.database import MILLISECONDS_PER_SECOND
from bookreviver.domain.errors import BookBusyError
from bookreviver.domain.spans import BlockKind, BlockOutcome, SpanAttribute, SpanName
from tests.harness.builders.domain import make_page
from tests.harness.concurrency.book_hold import hold_book_in_thread
from tests.harness.concurrency.wait_limits import IMPATIENT_SECONDS

if TYPE_CHECKING:
    from collections.abc import Callable

    from bookreviver.domain.books import BookRecord
    from bookreviver.domain.ids import BookId
    from bookreviver.ports.persistence import BookRepositories, CatalogueDatabase, CatalogueRepositories
    from tests.harness.adapters.database import LocatedBookDatabases
    from tests.harness.adapters.tracers import AskingTracer
    from tests.harness.fakes.recording_tracer import RecordingSpan

pytestmark = pytest.mark.anyio

FAILED_BLOCK: str = 'The block failed after it wrote a page.'
# Every duration a change that committed records
TIMINGS: tuple[SpanAttribute, ...] = (SpanAttribute.WAIT_MS, SpanAttribute.HOLD_MS, SpanAttribute.COMMIT_MS)
REFUSER_ARG: str = 'refuser'
# How many times the limit of a change its work lasts in the test that shows the limit never cuts the work
WORK_FACTOR: int = 3


class Refuser(StrEnum):
    """What refuses a change the writer, named as the test cases are."""

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
    """Tests for the span of one change of one catalogue database."""

    async def test_book_change_makes_one_span_of_its_book_with_its_timings(
        self, fx_traced_databases: Callable[..., LocatedBookDatabases], fx_book: BookId, fx_tracer: AskingTracer
    ) -> None:
        """Verify a committed ``Book.change`` gives one span with its kind, its book, its timings and its end.

        The wait is measured on the event loop and the holding and the commit in the writer thread, and each is a
        duration that cannot be negative.

        :param fx_traced_databases: Function opening the databases of the traced books.
        :type fx_traced_databases: Callable[..., LocatedBookDatabases]
        :param fx_book: Committed book of the database.
        :type fx_book: BookId
        :param fx_tracer: Tracer of the test.
        :type fx_tracer: AskingTracer
        """
        page = make_page()
        await fx_traced_databases().open(fx_book).change(lambda repos, _book_record: repos.pages.add(page))
        (span,) = fx_tracer.named(SpanName.WRITE_BLOCK)
        expect(span.attributes[SpanAttribute.BLOCK_KIND] is BlockKind.BOOK)
        expect(span.attributes[SpanAttribute.BOOK] == str(fx_book))
        expect(span.attributes[SpanAttribute.BLOCK_OUTCOME] is BlockOutcome.COMMITTED)
        for key in TIMINGS:
            expect(milliseconds(span, key) >= 0, f'{key} is negative')
        assert_expectations()

    async def test_block_is_a_child_of_the_span_current_at_its_caller(
        self, fx_traced_databases: Callable[..., LocatedBookDatabases], fx_book: BookId, fx_tracer: AskingTracer
    ) -> None:
        """Verify the ``write.block`` span, started on the event loop, has the span of its caller as its parent.

        The report of a run attributes the wait for the writer to a step through this link, so it is checked through
        the real adapter.

        :param fx_traced_databases: Function opening the databases of the traced books.
        :type fx_traced_databases: Callable[..., LocatedBookDatabases]
        :param fx_book: Committed book of the database.
        :type fx_book: BookId
        :param fx_tracer: Tracer of the test.
        :type fx_tracer: AskingTracer
        """
        page = make_page()
        with fx_tracer.span(SpanName.STEP):
            await fx_traced_databases().open(fx_book).change(lambda repos, _book_record: repos.pages.add(page))
        (step,) = fx_tracer.named(SpanName.STEP)
        (block,) = fx_tracer.named(SpanName.WRITE_BLOCK)
        assert block.parent is step

    async def test_block_that_fails_makes_a_span_marked_rolled_back(
        self, fx_traced_databases: Callable[..., LocatedBookDatabases], fx_book: BookId, fx_tracer: AskingTracer
    ) -> None:
        """Verify a change whose work raises is rolled back, and its span says so and has no commit time.

        :param fx_traced_databases: Function opening the databases of the traced books.
        :type fx_traced_databases: Callable[..., LocatedBookDatabases]
        :param fx_book: Committed book of the database.
        :type fx_book: BookId
        :param fx_tracer: Tracer of the test.
        :type fx_tracer: AskingTracer
        """

        def write_then_fail(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Write a page of the book, and fail before the work ends.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book.
            :type _book_record: BookRecord
            :raises RuntimeError: Always, after the page is written.
            """
            repos.pages.add(make_page())
            raise RuntimeError(FAILED_BLOCK)

        with pytest.raises(RuntimeError, match=FAILED_BLOCK):
            await fx_traced_databases().open(fx_book).change(write_then_fail)
        (span,) = fx_tracer.named(SpanName.WRITE_BLOCK)
        expect(span.attributes[SpanAttribute.BLOCK_OUTCOME] is BlockOutcome.ROLLED_BACK)
        expect(SpanAttribute.COMMIT_MS not in span.attributes)
        expect(milliseconds(span, SpanAttribute.WAIT_MS) >= 0)
        expect(milliseconds(span, SpanAttribute.HOLD_MS) >= 0)
        assert_expectations()

    async def test_change_is_of_the_other_kind_and_names_no_book(
        self, fx_traced_catalogue: Callable[..., CatalogueDatabase], fx_tracer: AskingTracer
    ) -> None:
        """Verify a change outside the content of a book is marked as the other kind, with no book.

        :param fx_traced_catalogue: Function opening a catalogue database over the traced catalogue and books.
        :type fx_traced_catalogue: Callable[..., CatalogueDatabase]
        :param fx_tracer: Tracer of the test.
        :type fx_tracer: AskingTracer
        """
        await fx_traced_catalogue().change(lambda _repos: None)
        (span,) = fx_tracer.named(SpanName.WRITE_BLOCK)
        expect(span.attributes[SpanAttribute.BLOCK_KIND] is BlockKind.OTHER)
        expect(SpanAttribute.BOOK not in span.attributes)
        assert_expectations()

    async def test_read_makes_no_write_block_span(
        self, fx_traced_databases: Callable[..., LocatedBookDatabases], fx_book: BookId, fx_tracer: AskingTracer
    ) -> None:
        """Verify a read leaves no ``write.block`` span, since it never asks for the writer.

        :param fx_traced_databases: Function opening the databases of the traced books.
        :type fx_traced_databases: Callable[..., LocatedBookDatabases]
        :param fx_book: Committed book of the database.
        :type fx_book: BookId
        :param fx_tracer: Tracer of the test.
        :type fx_tracer: AskingTracer
        """
        await fx_traced_databases().open(fx_book).read(lambda repos: repos.book_record.get())
        assert fx_tracer.named(SpanName.WRITE_BLOCK) == []

    async def test_limit_bounds_the_wait_for_the_writer_and_never_the_work(
        self, fx_traced_catalogue: Callable[..., CatalogueDatabase], fx_tracer: AskingTracer
    ) -> None:
        """Verify a work that lasts several times the limit of its change still commits, with its holding on the span.

        The writer is free when the change asks, so it waits for nothing. Its work then blocks the thread for longer
        than the limit, which would give up a change whose limit covered the work.

        :param fx_traced_catalogue: Function opening a catalogue database over the traced catalogue and books.
        :type fx_traced_catalogue: Callable[..., CatalogueDatabase]
        :param fx_tracer: Tracer of the test.
        :type fx_tracer: AskingTracer
        """
        work_seconds = IMPATIENT_SECONDS * WORK_FACTOR

        def last_longer_than_the_limit(_repos: CatalogueRepositories) -> None:
            """Block the thread of the change for longer than its limit.

            :param _repos: Repositories of the transaction, which this work does not use.
            :type _repos: CatalogueRepositories
            """
            threading.Event().wait(work_seconds)

        await fx_traced_catalogue(wait_seconds=IMPATIENT_SECONDS).change(last_longer_than_the_limit)
        (span,) = fx_tracer.named(SpanName.WRITE_BLOCK)
        expect(span.attributes[SpanAttribute.BLOCK_OUTCOME] is BlockOutcome.COMMITTED)
        expect(milliseconds(span, SpanAttribute.HOLD_MS) > IMPATIENT_SECONDS * MILLISECONDS_PER_SECOND)
        assert_expectations()


class TestWaitForTheWriter:
    """Tests for changes of two catalogue databases, of which the second asks while the first holds the writer."""

    async def test_second_block_waits_at_least_as_long_as_the_first_held_the_writer_after_it_began_to_wait(
        self, fx_traced_databases: Callable[..., LocatedBookDatabases], fx_book: BookId, fx_tracer: AskingTracer
    ) -> None:
        """Verify the wait of the second change covers what is left of the holding of the first, which ends later.

        The test notes the time when the second change is known to be asking, and the time just before the first is
        let go. The first cannot commit before the second asks, and the second cannot have the writer before the first
        commits, so the wait of the second is at least the time between the two notes.

        :param fx_traced_databases: Function opening the databases of the traced books.
        :type fx_traced_databases: Callable[..., LocatedBookDatabases]
        :param fx_book: Committed book of the database.
        :type fx_book: BookId
        :param fx_tracer: Tracer of the test.
        :type fx_tracer: AskingTracer
        """
        database = fx_traced_databases().open(fx_book)
        second_asking = fx_tracer.ask_after(2)
        # Both are set inside the task group, which the type checker cannot know runs its body to the end
        asking_seen = released = perf_counter()
        # The hold ends first, when the block ends, and the group then waits for the change that queued behind it
        async with anyio.create_task_group() as group, hold_book_in_thread(database):
            group.start_soon(partial(database.change, lambda _repos, _book_record: None))
            await second_asking.wait()
            asking_seen = perf_counter()
            await anyio.wait_all_tasks_blocked()
            released = perf_counter()

        # The second change cannot end before the first, so the spans end in the order of the changes
        held, waited = fx_tracer.named(SpanName.WRITE_BLOCK)
        still_held_ms = (released - asking_seen) * MILLISECONDS_PER_SECOND
        expect(milliseconds(waited, SpanAttribute.WAIT_MS) >= still_held_ms)
        expect(milliseconds(waited, SpanAttribute.WAIT_MS) > 0)
        expect(milliseconds(held, SpanAttribute.HOLD_MS) >= still_held_ms)
        assert_expectations()

    @pytest.mark.parametrize(REFUSER_ARG, list(Refuser), ids=list(Refuser))
    async def test_refused_block_ends_its_span_as_not_begun(
        self,
        fx_traced_databases: Callable[..., LocatedBookDatabases],
        fx_impatient_databases: Callable[..., LocatedBookDatabases],
        fx_book: BookId,
        fx_tracer: AskingTracer,
        refuser: Refuser,
    ) -> None:
        """Verify a change refused the writer ends its span as not begun, with no holding, and the holder commits.

        The asker's catalogue database has a limit of a few milliseconds. When the holder belongs to the other registry over
        the same books, each registry has its own queue for the book, as two processes would, so the asker has its
        writer at once and meets the holder at the write lock of SQLite, where it waits the busy timeout, which is that
        limit too, since the impatient registry opens with it. When both belong to the impatient registry, the asker
        never reaches SQLite: the queue of the book refuses it after the limit.

        :param fx_traced_databases: Function opening the databases of the books whose changes wait long.
        :type fx_traced_databases: Callable[..., LocatedBookDatabases]
        :param fx_impatient_databases: Function opening the databases of a registry of the same books, whose queues are its
                                     own and whose connections give up after a few milliseconds.
        :type fx_impatient_databases: Callable[..., LocatedBookDatabases]
        :param fx_book: Committed record of the book.
        :type fx_book: BookId
        :param fx_tracer: Tracer of the test.
        :type fx_tracer: AskingTracer
        :param refuser: What refuses the asking change, which decides the registry of the holder.
        :type refuser: Refuser
        """
        holder = fx_traced_databases() if refuser is Refuser.SQLITE else fx_impatient_databases()
        asker = fx_impatient_databases(wait_seconds=IMPATIENT_SECONDS)
        async with hold_book_in_thread(holder.open(fx_book)):
            with pytest.raises(BookBusyError):
                await asker.open(fx_book).change(lambda _repos, _book_record: None)
        refused, held = fx_tracer.named(SpanName.WRITE_BLOCK)
        expect(refused.attributes[SpanAttribute.BLOCK_OUTCOME] is BlockOutcome.NOT_BEGUN)
        expect(milliseconds(refused, SpanAttribute.WAIT_MS) > 0 or refuser is Refuser.SQLITE)
        expect(SpanAttribute.HOLD_MS not in refused.attributes)
        expect(held.attributes[SpanAttribute.BLOCK_OUTCOME] is BlockOutcome.COMMITTED)
        assert_expectations()
