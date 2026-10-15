"""Tests for ``bookreviver.app.trace_report``, run on a small file of spans written to the temporary directory.

The file holds the spans of a run of ten seconds with seven write blocks and five steps of two types. Four of the
blocks belong to no step: two overlap and one was refused the writer. Three blocks sit inside the two deskew steps that
ran. The names in it are the ones the application records, taken from the enumerations, so a span or an attribute that
is renamed there fails these tests instead of emptying the report.

The expected values are worked out by hand:

- The wall time is 10 s, from the start of the ``job`` span to its end.
- A block holds the lock from the end of its wait, through its holding and its commit. A, 0 to 2 s, waited nothing,
  held 1900 ms and committed in 100 ms, so it holds 0 to 2 s. B, 0.5 to 3 s, waited 500 ms, held 1900 ms and committed
  in 100 ms, so it holds 1 to 3 s. A and B overlap, so they hold the lock for 3 s together. C, 4 to 5.8 s, waited
  1000 ms, held 800 ms and rolled back, so it holds 5 to 5.8 s. D asked at 7 s, waited 50 ms and was refused the
  writer, so it began nothing and held nothing.
- Inside the first deskew step that ran, 8.00 to 8.10 s, block E asked at 8.02 s, waited 30 ms and held 30 ms, to
  8.08 s. Inside the second one, 8.20 to 8.50 s, block F asked at 8.21 s, waited 60 ms, held 70 ms and committed in
  10 ms, to 8.35 s, and block G asked at 8.40 s, waited 20 ms and held 30 ms, to 8.45 s. Together the holding is 3 + 0.8
  + 0.03 + 0.08 + 0.03 = 3.94 s, which is 39.4 % of the 10 s.
- There are 7 write blocks, and their waits are 0, 500, 1000, 50, 30, 60 and 20 ms, in order 0, 20, 30, 50, 60, 500 and
  1000 ms. By nearest rank over seven values the 50th percentile is the ceiling of 3.5, the 4th, 50 ms; the 90th is the
  ceiling of 6.3 and the 99th the ceiling of 6.93, both the 7th, 1000 ms; the maximum is 1000 ms.
- The six blocks that began give six holdings, in order 30, 30, 70, 800, 1900 and 1900 ms, so the 50th percentile is
  the 3rd, 70 ms, and the 90th and the 99th are the 6th, 1900 ms, which is also the maximum. The five blocks that
  committed give five commits, in order 0, 0, 10, 100 and 100 ms, so the 50th percentile is the ceiling of 2.5, the 3rd,
  10 ms, and the 90th and the 99th are the ceilings of 4.5 and 4.95, the 5th, 100 ms, which is also the maximum.
- ``geometry.deskew`` has four steps: two cached, and two that ran in 100 and 300 ms, so the median run is 200 ms.
  The wait for the lock inside the step of 100 ms is that of E, 30 ms, and inside the step of 300 ms the sum of F and G,
  60 + 20 = 80 ms, so the median wait is (30 + 80) / 2 = 55 ms. ``split.none`` has one step that ran in 40 ms, with no
  block inside it, so its median run is 40 ms and its median wait is 0 ms.

The line of the event-loop lag is tried on a file of its own, made of ``loop.lag`` spans of known lags and one block,
and its absence on the file above, which has no such span. One test writes its file with the OpenTelemetry adapter
instead of by hand, so the parser is also tried on what the real exporter writes.

The budgets of ``--check`` are tried on files of their own, built from the same builders: the preview budget reads the
``job`` spans of the kind ``preview-step`` and nothing else, so a job of another kind, or steps at the preview scale
with no job around them, do not count, however long they lasted. A preview job counts for its whole duration, which
holds all of its steps. The verdict lines hold the limit and the measured value, which are worked out in the cases.
"""

import json
from datetime import UTC, datetime, timedelta
from itertools import count
from typing import TYPE_CHECKING, NamedTuple

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.tracing.opentelemetry_tracer import OpenTelemetryTracer
from bookreviver.app import trace_report
from bookreviver.domain.enums import BlockOutcome, JobKind, SpanAttribute, SpanName, StepOutcome, VersionScale

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence
    from pathlib import Path

START: datetime = datetime(2026, 1, 1, tzinfo=UTC)
FILE_NAME: str = 'spans.jsonl'
ENCODING: str = 'utf-8'
TIME_FORMAT: str = '%Y-%m-%dT%H:%M:%S.%fZ'
DESKEW: str = 'geometry.deskew'
SPLIT: str = 'split.none'
TRACE_ID: str = '0x' + '1' * 32
# The status of a span no exception left, which the exporter writes for every span
UNSET_STATUS: str = 'UNSET'
# Identifiers of the spans a test refers to as parents; the spans nobody refers to take the next one of the counter
SPAN_IDS: Iterator[int] = count(1)
JOB_NAME: str = 'job'
JOB_ID: str = '0x00000000000000ff'
FIRST_STEP_ID: str = '0x00000000000000a1'
SECOND_STEP_ID: str = '0x00000000000000a2'
EXPECTED_REPORT: str = f"""Trace report: {FILE_NAME}
Wall time of the run, s: 10.000
Write blocks: 7
Share of the run with the write lock held, %: 39.4
Wait for the write lock, ms: p50 50.00, p90 1000.00, p99 1000.00, max 1000.00
Hold of a block, ms: p50 70.00, p90 1900.00, p99 1900.00, max 1900.00
Commit of a block, ms: p50 10.00, p90 100.00, p99 100.00, max 100.00

| Step type       | Count | Found cached, % | Median run, ms | Median wait for the lock, ms |
|-----------------|-------|-----------------|----------------|------------------------------|
| {DESKEW} | 4     | 50.0            | 200.0          | 55.0                         |
| {SPLIT}      | 1     | 0.0             | 40.0           | 0.0                          |"""
# The lags of the probe ticks of one test, and the two lines the report prints for them and for one block of 20 ms
LAG_VALUES_MS: tuple[float, ...] = (1.0, 2.0, 3.0, 4.0, 10.0)
WAIT_LINE: str = 'Wait for the write lock, ms: p50 20.00, p90 20.00, p99 20.00, max 20.00'
LAG_LINE: str = 'Event-loop lag, ms: p50 3.00, p90 10.00, p99 10.00, max 10.00'
# The wait the block of the test that reads the exporter's file records, which one decimal shows exactly
WRITTEN_WAIT_MS: float = 12.5


class Lineage(NamedTuple):
    """The identifier of a span and of the span it was started inside.

    :ivar span_id: Identifier of the span, or None to take the next one of the counter.
    :ivar parent_id: Identifier of the parent, or None for the root of a trace.
    """

    span_id: str | None = None
    parent_id: str | None = None


class Placement(NamedTuple):
    """Where a step span is in the run and what its identifier is.

    :ivar start_s: Seconds after the start of the run at which the step started.
    :ivar span_id: Identifier of the step, or None to take the next one of the counter.
    :ivar parent_id: Identifier of the span the step was started inside, or None for a root.
    """

    start_s: float = 1.0
    span_id: str | None = None
    parent_id: str | None = None


class BlockTimes(NamedTuple):
    """The wait of a write block and the parts of its work in milliseconds, each part written only when it is given.

    :ivar wait_ms: Wait for the writer, which every block records.
    :ivar hold_ms: From the beginning of the transaction until the commit or the rollback began.
    :ivar commit_ms: The ``COMMIT`` statement.
    """

    wait_ms: int
    hold_ms: int | None = None
    commit_ms: int | None = None


# A span of the next identifier of the counter that has no parent, and a step that starts one second into the run
NEXT_LINEAGE: Lineage = Lineage()
ONE_SECOND_IN: Placement = Placement()
# The attribute of each field of BlockTimes, in the order of the fields
TIME_KEYS: tuple[SpanAttribute, ...] = (SpanAttribute.WAIT_MS, SpanAttribute.HOLD_MS, SpanAttribute.COMMIT_MS)


def span_line(
    name: SpanName,
    start_s: float,
    end_s: float,
    attributes: Mapping[SpanAttribute, object],
    *,
    lineage: Lineage = NEXT_LINEAGE,
) -> str:
    """Write one span as the exporter does, as a line of JSON.

    :param name: Name of the span.
    :type name: SpanName
    :param start_s: Seconds after the start of the run at which the span started.
    :type start_s: float
    :param end_s: Seconds after the start of the run at which the span ended.
    :type end_s: float
    :param attributes: Facts of the span.
    :type attributes: Mapping[SpanAttribute, object]
    :param lineage: The identifier of the span and of its parent.
    :type lineage: Lineage
    :returns: The line, without the end of line.
    :rtype: str
    """
    record = {
        'name': name.value,
        'context': {
            'trace_id': TRACE_ID,
            'span_id': lineage.span_id or f'0x{next(SPAN_IDS):016x}',
            'trace_state': '[]',
        },
        'parent_id': lineage.parent_id,
        'start_time': (START + timedelta(seconds=start_s)).strftime(TIME_FORMAT),
        'end_time': (START + timedelta(seconds=end_s)).strftime(TIME_FORMAT),
        'attributes': {key.value: value for key, value in attributes.items()},
        'status': {'status_code': UNSET_STATUS},
    }
    return json.dumps(record)


def block(
    start_s: float,
    end_s: float,
    outcome: BlockOutcome,
    times: BlockTimes,
    *,
    parent_id: str | None = None,
) -> str:
    """Write a ``write.block`` span.

    :param start_s: Seconds after the start of the run at which the block asked for the lock.
    :type start_s: float
    :param end_s: Seconds after the start of the run at which the block gave up its turn.
    :type end_s: float
    :param outcome: How the block ended.
    :type outcome: BlockOutcome
    :param times: The wait of the block and the parts of it the span records.
    :type times: BlockTimes
    :param parent_id: Identifier of the span the block was started inside, or None for a root.
    :type parent_id: str | None
    :returns: The line of the span.
    :rtype: str
    """
    attributes: dict[SpanAttribute, object] = {SpanAttribute.BLOCK_OUTCOME: outcome.value}
    attributes.update((key, float(value)) for key, value in zip(TIME_KEYS, times, strict=True) if value is not None)
    return span_line(SpanName.WRITE_BLOCK, start_s, end_s, attributes, lineage=Lineage(parent_id=parent_id))


def step(
    step_type: str,
    outcome: StepOutcome,
    run_ms: int,
    scale: VersionScale = VersionScale.FULL,
    *,
    placement: Placement = ONE_SECOND_IN,
) -> str:
    """Write a ``step`` span of the given duration, which by default starts one second into the run.

    :param step_type: Key of the processor of the step.
    :type step_type: str
    :param outcome: How the step was settled.
    :type outcome: StepOutcome
    :param run_ms: Milliseconds the step lasted.
    :type run_ms: int
    :param scale: The size of the image the step ran on.
    :type scale: VersionScale
    :param placement: When the step started and what its identifier is.
    :type placement: Placement
    :returns: The line of the span.
    :rtype: str
    """
    attributes = {
        SpanAttribute.STEP_TYPE: step_type,
        SpanAttribute.STEP_SCALE: scale.value,
        SpanAttribute.STEP_OUTCOME: outcome.value,
    }
    end_s = placement.start_s + run_ms / 1000
    lineage = Lineage(span_id=placement.span_id, parent_id=placement.parent_id)
    return span_line(SpanName.STEP, placement.start_s, end_s, attributes, lineage=lineage)


def job(kind: JobKind, run_ms: int, *, lineage: Lineage = NEXT_LINEAGE) -> str:
    """Write a ``job`` span of the given kind that starts at the start of the run.

    :param kind: What the job does.
    :type kind: JobKind
    :param run_ms: Milliseconds the job lasted.
    :type run_ms: int
    :param lineage: The identifier of the job and of its parent.
    :type lineage: Lineage
    :returns: The line of the span.
    :rtype: str
    """
    attributes = {SpanAttribute.JOB_KIND: kind.value, SpanAttribute.JOB_ID: JOB_NAME}
    return span_line(SpanName.JOB, 0.0, run_ms / 1000, attributes, lineage=lineage)


@pytest.fixture
def fx_write_spans(tmp_path: Path) -> Callable[[Sequence[str]], Path]:
    """Return the function that writes lines of spans to the file of the test.

    :param tmp_path: Temporary directory of the test.
    :type tmp_path: Path
    :returns: The function, which takes the lines and returns the file.
    :rtype: Callable[[Sequence[str]], Path]
    """

    def write(lines: Sequence[str]) -> Path:
        """Write the lines to the file of the test, one on each line of the file.

        :param lines: The lines of the spans.
        :type lines: Sequence[str]
        :returns: The file.
        :rtype: Path
        """
        path = tmp_path / FILE_NAME
        path.write_text('\n'.join(lines) + '\n', encoding=ENCODING)
        return path

    return write


@pytest.fixture
def fx_span_file(fx_write_spans: Callable[[Sequence[str]], Path]) -> Path:
    """Write the file of spans the tests of the report read.

    :param fx_write_spans: Function that writes lines of spans to a file.
    :type fx_write_spans: Callable[[Sequence[str]], Path]
    :returns: The file.
    :rtype: Path
    """
    return fx_write_spans(
        [
            span_line(SpanName.JOB, 0.0, 10.0, {SpanAttribute.JOB_ID: JOB_NAME}, lineage=Lineage(span_id=JOB_ID)),
            block(0.0, 2.0, BlockOutcome.COMMITTED, BlockTimes(0, 1900, 100), parent_id=JOB_ID),
            block(0.5, 3.0, BlockOutcome.COMMITTED, BlockTimes(500, 1900, 100), parent_id=JOB_ID),
            block(4.0, 5.8, BlockOutcome.ROLLED_BACK, BlockTimes(1000, 800), parent_id=JOB_ID),
            block(7.0, 7.05, BlockOutcome.NOT_BEGUN, BlockTimes(50), parent_id=JOB_ID),
            step(DESKEW, StepOutcome.CACHED, 5, placement=Placement(9.0)),
            step(DESKEW, StepOutcome.RAN, 100, placement=Placement(8.0, FIRST_STEP_ID)),
            step(DESKEW, StepOutcome.RAN, 300, placement=Placement(8.2, SECOND_STEP_ID)),
            step(DESKEW, StepOutcome.CACHED, 8, placement=Placement(9.1)),
            step(SPLIT, StepOutcome.RAN, 40, placement=Placement(9.5)),
            block(8.02, 8.08, BlockOutcome.COMMITTED, BlockTimes(30, 30, 0), parent_id=FIRST_STEP_ID),
            block(8.21, 8.35, BlockOutcome.COMMITTED, BlockTimes(60, 70, 10), parent_id=SECOND_STEP_ID),
            block(8.4, 8.45, BlockOutcome.COMMITTED, BlockTimes(20, 30, 0), parent_id=SECOND_STEP_ID),
        ]
    )


class CheckCase(NamedTuple):
    """A file of spans for ``--check`` and what the command is expected to print and return.

    :ivar lines: The lines of the spans of the file.
    :ivar status: The exit status expected.
    :ivar preview: The part of the line of the preview budget after its limit.
    :ivar lock_wait: The part of the line of the lock wait budget after its limit.
    """

    lines: tuple[str, ...]
    status: int
    preview: str
    lock_wait: str


PREVIEW_LINE: str = 'Preview job on the visible page, max, ms: limit 1000.00, '
LOCK_WAIT_LINE: str = 'Wait for the write lock, p99, ms: limit 100.00, '
CHECK_CASES: dict[str, CheckCase] = {
    # The longest preview job is 400 ms, and of the waits of 0, 20 and 90 ms the 99th percentile is the largest
    'both-met': CheckCase(
        lines=(
            job(JobKind.PREVIEW_STEP, 250),
            job(JobKind.PREVIEW_STEP, 400),
            block(0.0, 1.0, BlockOutcome.COMMITTED, BlockTimes(0)),
            block(1.0, 2.0, BlockOutcome.COMMITTED, BlockTimes(20)),
            block(2.0, 3.0, BlockOutcome.COMMITTED, BlockTimes(90)),
        ),
        status=0,
        preview='measured 400.00, met',
        lock_wait='measured 90.00, met',
    ),
    'lock-wait-exceeded': CheckCase(
        lines=(
            job(JobKind.PREVIEW_STEP, 400),
            block(0.0, 1.0, BlockOutcome.COMMITTED, BlockTimes(10)),
            block(1.0, 2.0, BlockOutcome.COMMITTED, BlockTimes(250)),
        ),
        status=1,
        preview='measured 400.00, met',
        lock_wait='measured 250.00, exceeded',
    ),
    'preview-exceeded': CheckCase(
        lines=(job(JobKind.PREVIEW_STEP, 1_500), block(0.0, 1.0, BlockOutcome.COMMITTED, BlockTimes(10))),
        status=1,
        preview='measured 1500.00, exceeded',
        lock_wait='measured 10.00, met',
    ),
    # Each of the two steps took 600 ms, which the old measure of the longest step called met, and the reader waited for
    # both of them in one job of 1.2 s
    'job-of-two-steps-exceeded': CheckCase(
        lines=(
            job(JobKind.PREVIEW_STEP, 1_200, lineage=Lineage(span_id=JOB_ID)),
            step(DESKEW, StepOutcome.RAN, 600, VersionScale.PREVIEW, placement=Placement(0.0, parent_id=JOB_ID)),
            step(DESKEW, StepOutcome.RAN, 600, VersionScale.PREVIEW, placement=Placement(0.6, parent_id=JOB_ID)),
            block(0.0, 1.0, BlockOutcome.COMMITTED, BlockTimes(10)),
        ),
        status=1,
        preview='measured 1200.00, exceeded',
        lock_wait='measured 10.00, met',
    ),
    # A job of another kind took 6 s, and steps at the preview scale with no preview job around them took 5 s and
    # 300 ms, and none of them is a preview job
    'other-jobs-and-bare-steps-ignored': CheckCase(
        lines=(
            job(JobKind.RUN_STAGE, 6_000),
            job(JobKind.PREVIEW_STEP, 300),
            step(DESKEW, StepOutcome.RAN, 5_000, VersionScale.PREVIEW),
            block(0.0, 1.0, BlockOutcome.COMMITTED, BlockTimes(10)),
        ),
        status=0,
        preview='measured 300.00, met',
        lock_wait='measured 10.00, met',
    ),
    'no-write-blocks': CheckCase(
        lines=(job(JobKind.PREVIEW_STEP, 400),),
        status=0,
        preview='measured 400.00, met',
        lock_wait='measured -, no data',
    ),
    'no-preview-jobs': CheckCase(
        lines=(
            job(JobKind.RUN_STAGE, 6_000),
            step(DESKEW, StepOutcome.RAN, 400, VersionScale.PREVIEW),
            block(0.0, 1.0, BlockOutcome.COMMITTED, BlockTimes(10)),
        ),
        status=0,
        preview='measured -, no data',
        lock_wait='measured 10.00, met',
    ),
}


class TestMergedLength:
    """Tests for the union of intervals, which counts what overlaps once."""

    def test_overlapping_and_separate_intervals_are_added_up_once(self) -> None:
        """Verify the union of 0 to 2, 1 to 3 and 5 to 6 is 3 + 1 seconds, and an interval inside another adds none."""
        intervals = [(5.0, 6.0), (1.0, 3.0), (0.0, 2.0), (0.5, 1.5)]
        assert trace_report.merged_length(intervals) == pytest.approx(4.0)

    def test_no_interval_has_no_length(self) -> None:
        """Verify the union of nothing is nothing."""
        assert trace_report.merged_length([]) == pytest.approx(0.0)


class TestPercentile:
    """Tests for the nearest-rank percentile."""

    @pytest.mark.parametrize(('percent', 'expected'), [(50, 50.0), (90, 1000.0), (99, 1000.0), (100, 1000.0)], ids=str)
    def test_percentile_is_the_smallest_value_that_covers_the_share(self, percent: int, expected: float) -> None:
        """Verify the rank is the ceiling of the share of the count, so the value is one that was observed.

        :param percent: The percentile.
        :type percent: int
        :param expected: The value of the nearest rank among 0, 50, 500 and 1000.
        :type expected: float
        """
        assert trace_report.percentile([1000.0, 0.0, 500.0, 50.0], percent) == expected


class TestReport:
    """Tests for the text of the report of the file of spans."""

    def test_report_gives_the_hand_worked_figures_of_the_run(self, fx_span_file: Path) -> None:
        """Verify the wall time, the merged share of the holding, the waits and the table of the steps.

        :param fx_span_file: File of the spans of a run.
        :type fx_span_file: Path
        """
        spans = trace_report.load_spans(fx_span_file)
        assert trace_report.report(spans, fx_span_file.name) == EXPECTED_REPORT

    def test_wait_of_blocks_outside_the_steps_that_ran_is_not_counted_in_a_step(
        self, fx_write_spans: Callable[[Sequence[str]], Path]
    ) -> None:
        """Verify only the write blocks whose parent is a step that ran add to the wait of the steps of its type.

        The file holds one step that ran and one found cached, both of deskew. A block of 400 ms is inside the cached
        step and one of 700 ms inside the job, so neither is the wait of a step that ran, and the median wait of the
        row is 0 ms, where the median run is that of the one step that ran, 100 ms.

        :param fx_write_spans: Function that writes lines of spans to a file.
        :type fx_write_spans: Callable[[Sequence[str]], Path]
        """
        path = fx_write_spans(
            [
                span_line(SpanName.JOB, 0.0, 10.0, {SpanAttribute.JOB_ID: JOB_NAME}, lineage=Lineage(span_id=JOB_ID)),
                step(DESKEW, StepOutcome.RAN, 100, placement=Placement(1.0)),
                step(DESKEW, StepOutcome.CACHED, 5, placement=Placement(2.0, FIRST_STEP_ID)),
                block(2.0, 2.4, BlockOutcome.COMMITTED, BlockTimes(400), parent_id=FIRST_STEP_ID),
                block(5.0, 6.0, BlockOutcome.COMMITTED, BlockTimes(700), parent_id=JOB_ID),
            ]
        )
        spans = trace_report.load_spans(path)
        row = trace_report.report(spans, path.name).splitlines()[-1]
        assert [cell.strip() for cell in row.strip('|').split('|')] == [DESKEW, '2', '50.0', '100.0', '0.0']

    def test_lag_line_of_the_probe_follows_the_lock_wait_line(
        self, fx_write_spans: Callable[[Sequence[str]], Path]
    ) -> None:
        """Verify the percentiles of the ``loop.lag`` spans are printed on the line after the wait for the lock.

        The lags are 1, 2, 3, 4 and 10 ms, so by nearest rank over five values the 50th percentile is the 3rd, 3 ms,
        the 90th and the 99th are the 5th, 10 ms, and the maximum is 10 ms. The wait line comes from the one block.

        :param fx_write_spans: Function that writes lines of spans to a file.
        :type fx_write_spans: Callable[[Sequence[str]], Path]
        """
        ticks = [
            span_line(SpanName.LOOP_LAG, start_s, start_s + 0.1, {SpanAttribute.LAG_MS: lag_ms})
            for start_s, lag_ms in enumerate(LAG_VALUES_MS)
        ]
        path = fx_write_spans([block(0.0, 1.0, BlockOutcome.COMMITTED, BlockTimes(20)), *ticks])
        lines = trace_report.report(trace_report.load_spans(path), path.name).splitlines()
        assert lines[4:6] == [WAIT_LINE, LAG_LINE]

    def test_report_reads_the_file_the_exporter_writes(self, tmp_path: Path) -> None:
        """Verify the report of a file the real tracer wrote has the step type and the wait of the block inside it.

        The hand-written lines of the other tests are what the author thinks the exporter writes. Here the file comes
        from the OpenTelemetry adapter, so the parser of the report meets the real format: the times, the identifiers
        of a span and of its parent, and the status.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path = tmp_path / FILE_NAME
        step_attributes = {
            SpanAttribute.STEP_TYPE: DESKEW,
            SpanAttribute.STEP_SCALE: VersionScale.FULL,
            SpanAttribute.STEP_OUTCOME: StepOutcome.RAN,
        }
        with OpenTelemetryTracer(path) as tracer, tracer.span(SpanName.STEP, step_attributes):
            written = tracer.start(SpanName.WRITE_BLOCK)
            written.set_attribute(SpanAttribute.WAIT_MS, WRITTEN_WAIT_MS)
            written.end()
        spans = trace_report.load_spans(path)
        row = trace_report.report(spans, path.name).splitlines()[-1]
        cells = [cell.strip() for cell in row.strip('|').split('|')]
        expect(cells[0] == DESKEW)
        expect(cells[-1] == f'{WRITTEN_WAIT_MS:{trace_report.ONE_DECIMAL}}')
        expect(not any(span.failed for span in spans))
        assert_expectations()

    def test_report_without_probe_spans_has_no_lag_line(self, fx_span_file: Path) -> None:
        """Verify a run recorded without the probe prints no line of the lag, instead of one made of zeros.

        :param fx_span_file: File of the spans of a run, which holds no ``loop.lag`` span.
        :type fx_span_file: Path
        """
        report = trace_report.report(trace_report.load_spans(fx_span_file), fx_span_file.name)
        assert 'Event-loop lag' not in report

    def test_main_prints_the_report_and_succeeds(self, fx_span_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Verify the command line prints the report of the file it is given and exits with 0, budgets or not.

        The lock wait of this file is far over its budget, and without ``--check`` nothing is compared.

        :param fx_span_file: File of the spans of a run.
        :type fx_span_file: Path
        :param capsys: Fixture that captures what the script writes.
        :type capsys: pytest.CaptureFixture[str]
        """
        status = trace_report.main([str(fx_span_file)])
        printed = capsys.readouterr()
        expect(status == 0)
        expect(printed.out == EXPECTED_REPORT + '\n')
        expect(printed.err == '')
        assert_expectations()

    def test_main_refuses_a_file_without_spans(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Verify an empty file is reported on the error stream and fails, instead of printing zeros.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param capsys: Fixture that captures what the script writes.
        :type capsys: pytest.CaptureFixture[str]
        """
        empty = tmp_path / FILE_NAME
        empty.write_text('\n', encoding=ENCODING)
        status = trace_report.main([str(empty)])
        printed = capsys.readouterr()
        expect(status == 1)
        expect(printed.out == '')
        expect('holds no span' in printed.err)
        assert_expectations()


class TestCheck:
    """Tests for the comparison of the run with the budgets, which ``--check`` adds to the report."""

    @pytest.mark.parametrize('case', CHECK_CASES.values(), ids=CHECK_CASES.keys())
    def test_check_prints_a_verdict_for_each_budget_and_fails_when_one_is_exceeded(
        self,
        fx_write_spans: Callable[[Sequence[str]], Path],
        capsys: pytest.CaptureFixture[str],
        case: CheckCase,
    ) -> None:
        """Verify the report is followed by a line for each budget, and the status is 1 only for an exceeded budget.

        :param fx_write_spans: Function that writes lines of spans to a file.
        :type fx_write_spans: Callable[[Sequence[str]], Path]
        :param capsys: Fixture that captures what the script writes.
        :type capsys: pytest.CaptureFixture[str]
        :param case: The spans of the file and what the command has to print and return.
        :type case: CheckCase
        """
        path = fx_write_spans(case.lines)
        status = trace_report.main([str(path), '--check'])
        printed = capsys.readouterr()
        expect(status == case.status)
        expect(printed.out.startswith(f'Trace report: {FILE_NAME}\n'))
        expect(printed.out.splitlines()[-2:] == [PREVIEW_LINE + case.preview, LOCK_WAIT_LINE + case.lock_wait])
        expect(printed.err == '')
        assert_expectations()
