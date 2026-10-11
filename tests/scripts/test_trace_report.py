"""Tests for ``scripts/trace_report.py``, run on a small file of spans written to the temporary directory.

The file holds the spans of a run of ten seconds with four write blocks, of which two overlap and one never took the
lock, and five steps of two types. The names in it are the ones the application records, taken from the enumerations,
so a span or an attribute that is renamed there fails these tests instead of emptying the report.

The expected values are worked out by hand:

- The wall time is 10 s, from the start of the ``job`` span to its end.
- The blocks hold the lock from the end of their wait to their end: A over 0 to 2 s, B over 1 to 3 s (it asked at 0.5 s
  and waited 500 ms), C over 5 to 6 s (it asked at 4 s and waited 1000 ms). A and B overlap, so they hold the lock for
  3 s together, and with C for 4 s: 40 % of the run, where adding the three up would give 50 %. The fourth block asked at
  7 s, waited 50 ms and was refused, so it held nothing.
- The waits are 0, 500, 1000 and 50 ms. By nearest rank over four values the 50th percentile is the second smallest,
  50 ms, and the 90th and 99th are the largest, 1000 ms.
- ``geometry.deskew`` has four steps: two cached, and two that ran in 100 and 300 ms, so the median run is 200 ms.
  ``split.none`` has one step that ran in 40 ms.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import BlockOutcome, SpanAttribute, SpanName, StepOutcome
from scripts import trace_report

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

START: datetime = datetime(2026, 1, 1, tzinfo=UTC)
FILE_NAME: str = 'spans.jsonl'
ENCODING: str = 'utf-8'
TIME_FORMAT: str = '%Y-%m-%dT%H:%M:%S.%fZ'
DESKEW: str = 'geometry.deskew'
SPLIT: str = 'split.none'
EXPECTED_REPORT: str = f"""Trace report: {FILE_NAME}
Wall time of the run, s: 10.000
Write blocks: 4
Share of the run with the write lock held, %: 40.0
Wait for the write lock, ms: p50 50.00, p90 1000.00, p99 1000.00, max 1000.00

| Step type       | Count | Found cached, % | Median run, ms |
|-----------------|-------|-----------------|----------------|
| {DESKEW} | 4     | 50.0            | 200.0          |
| {SPLIT}      | 1     | 0.0             | 40.0           |"""


def span_line(name: SpanName, start_s: float, end_s: float, attributes: Mapping[SpanAttribute, object]) -> str:
    """Write one span as the exporter does, as a line of JSON.

    :param name: Name of the span.
    :type name: SpanName
    :param start_s: Seconds after the start of the run at which the span started.
    :type start_s: float
    :param end_s: Seconds after the start of the run at which the span ended.
    :type end_s: float
    :param attributes: Facts of the span.
    :type attributes: Mapping[SpanAttribute, object]
    :returns: The line, without the end of line.
    :rtype: str
    """
    record = {
        'name': name.value,
        'start_time': (START + timedelta(seconds=start_s)).strftime(TIME_FORMAT),
        'end_time': (START + timedelta(seconds=end_s)).strftime(TIME_FORMAT),
        'attributes': {key.value: value for key, value in attributes.items()},
    }
    return json.dumps(record)


def block(start_s: float, end_s: float, wait_ms: int, outcome: BlockOutcome) -> str:
    """Write a ``write.block`` span.

    :param start_s: Seconds after the start of the run at which the block asked for the lock.
    :type start_s: float
    :param end_s: Seconds after the start of the run at which the block ended.
    :type end_s: float
    :param wait_ms: Milliseconds the block waited for the lock.
    :type wait_ms: int
    :param outcome: How the block ended.
    :type outcome: BlockOutcome
    :returns: The line of the span.
    :rtype: str
    """
    attributes = {SpanAttribute.WAIT_MS: float(wait_ms), SpanAttribute.BLOCK_OUTCOME: outcome.value}
    return span_line(SpanName.WRITE_BLOCK, start_s, end_s, attributes)


def step(step_type: str, outcome: StepOutcome, run_ms: int) -> str:
    """Write a ``step`` span of the given duration, which starts one second into the run.

    :param step_type: Key of the processor of the step.
    :type step_type: str
    :param outcome: How the step was settled.
    :type outcome: StepOutcome
    :param run_ms: Milliseconds the step lasted.
    :type run_ms: int
    :returns: The line of the span.
    :rtype: str
    """
    attributes = {SpanAttribute.STEP_TYPE: step_type, SpanAttribute.STEP_OUTCOME: outcome.value}
    return span_line(SpanName.STEP, 1.0, 1.0 + run_ms / 1000, attributes)


@pytest.fixture
def fx_span_file(tmp_path: Path) -> Path:
    """Write the file of spans the tests of this module read.

    :param tmp_path: Temporary directory of the test.
    :type tmp_path: Path
    :returns: The file.
    :rtype: Path
    """
    lines = [
        span_line(SpanName.JOB, 0.0, 10.0, {SpanAttribute.JOB_ID: 'job'}),
        block(0.0, 2.0, 0, BlockOutcome.COMMITTED),
        block(0.5, 3.0, 500, BlockOutcome.COMMITTED),
        block(4.0, 6.0, 1000, BlockOutcome.ROLLED_BACK),
        block(7.0, 7.5, 50, BlockOutcome.NOT_BEGUN),
        step(DESKEW, StepOutcome.CACHED, 5),
        step(DESKEW, StepOutcome.RAN, 100),
        step(DESKEW, StepOutcome.RAN, 300),
        step(DESKEW, StepOutcome.CACHED, 8),
        step(SPLIT, StepOutcome.RAN, 40),
    ]
    path = tmp_path / FILE_NAME
    path.write_text('\n'.join(lines) + '\n', encoding=ENCODING)
    return path


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

    def test_main_prints_the_report_and_succeeds(self, fx_span_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Verify the command line prints the report of the file it is given and exits with 0.

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
