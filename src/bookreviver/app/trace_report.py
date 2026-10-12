"""Summarise the spans of one run of the backend, written by its measurements as a JSON lines file.

Usage: ``uv run bookreviver-trace-report PATH [--check]``

The file is the one ``BOOKREVIVER_TRACING__EXPORT_PATH`` names. It holds one OpenTelemetry span per line, and the
report is meant to be pasted into a note:

- the wall time of the run, from the start of the first span to the end of the last;
- how much of that time some write block held the write lock of the database, where blocks that overlap are counted
  once;
- the number of write blocks and the wait for the lock, as the 50th, 90th and 99th percentile and the maximum;
- the lag of the event loop in the same percentiles, when the run has ``loop.lag`` spans;
- for every type of step, how many steps there were, how many of them were found made by an earlier run, and, over those
  that ran, the median milliseconds and the median of the wait for the write lock inside the step, which is the sum of
  the waits of the write blocks whose parent is the step.

A percentile is the nearest rank: the smallest value that at least that share of the values does not exceed.

The durations of a write block are measured on the event loop, as the time from a request of the block until the loop
resumed it, so each includes the delay of the loop. The ``loop.lag`` spans show how long that delay was during the run,
and a wait for the lock that is no longer than the lag of the loop says nothing about the lock.

With ``--check`` the command goes on to compare the run with two budgets and prints a line for each, with the limit, the
measured value and the verdict, ``met``, ``exceeded`` or ``no data`` where the run has nothing to measure. It exits with
1 when a budget is exceeded:

- a preview on the visible page takes under 1 s, measured as the longest ``job`` span of the kind ``preview-step``,
  which is the whole preview job with every step of it and the waits for the lock in them, because the budget
  NFR-PERF-3 is the latency a reader of the page sees. The time the job waited in the queue before it started is not in
  the span;
- the wait for the write lock stays under 100 ms at the 99th percentile over all write blocks.
"""

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from attrs import frozen

from bookreviver.domain.enums import BlockOutcome, JobKind, SpanAttribute, SpanName, StepOutcome

MILLISECONDS_PER_SECOND: int = 1_000
PERCENT: int = 100
PERCENTILES: tuple[int, ...] = (50, 90, 99)
NO_VALUE: str = '-'
FIELD_SEPARATOR: str = ', '
# What stands between the name of a line of the report and what it measures
LABEL_SEPARATOR: str = ': '
# Format specifications of the numbers of the report
ONE_DECIMAL: str = '.1f'
TWO_DECIMALS: str = '.2f'
STEP_HEADER: tuple[str, ...] = (
    'Step type',
    'Count',
    'Found cached, %',
    'Median run, ms',
    'Median wait for the lock, ms',
)
# The verdicts of the budgets
MET: str = 'met'
EXCEEDED: str = 'exceeded'
NO_DATA: str = 'no data'
LOCK_WAIT_PERCENT: int = 99


@frozen
class Budget:
    """A limit on one measurement of a run, which the measured value has to stay under.

    :ivar name: What is limited, with the statistic and the unit.
    :ivar limit_ms: The limit in milliseconds, which a value equal to it already exceeds.
    """

    name: str
    limit_ms: float


# What a preview job on the visible page may take: the budget NFR-PERF-3, which the test of the preview reads too
PREVIEW_BUDGET: Budget = Budget(name='Preview job on the visible page, max, ms', limit_ms=1_000.0)
LOCK_WAIT_BUDGET: Budget = Budget(name=f'Wait for the write lock, p{LOCK_WAIT_PERCENT}, ms', limit_ms=100.0)


@frozen
class Span:
    """One span of the file.

    :ivar name: Name of the span, such as ``write.block``.
    :ivar start: Seconds since the epoch at which the span started.
    :ivar end: Seconds since the epoch at which the span ended.
    :ivar span_id: Identifier of the span, which the spans started inside it name as their parent.
    :ivar parent_id: Identifier of the span this one was started inside, or None for the root of a trace.
    :ivar attributes: Facts the span carries.
    """

    name: str
    start: float
    end: float
    span_id: str
    parent_id: str | None
    attributes: dict[str, str | float]

    @property
    def duration_ms(self) -> float:
        """Milliseconds from the start to the end of the span."""
        return (self.end - self.start) * MILLISECONDS_PER_SECOND


def load_spans(path: Path) -> list[Span]:
    """Read the spans of a file, one JSON object per non-empty line.

    :param path: The JSON lines file of a run.
    :type path: Path
    :returns: The spans, in the order of the file.
    :rtype: list[Span]
    """
    spans: list[Span] = []
    with path.open(encoding='utf-8') as lines:
        for line in lines:
            if not line.strip():
                continue
            record = json.loads(line)
            start = datetime.fromisoformat(record['start_time']).timestamp()
            end = datetime.fromisoformat(record['end_time']).timestamp()
            spans.append(
                Span(
                    name=record['name'],
                    start=start,
                    end=end,
                    span_id=record['context']['span_id'],
                    parent_id=record.get('parent_id'),
                    attributes=record.get('attributes', {}),
                )
            )
    return spans


def merged_length(intervals: list[tuple[float, float]]) -> float:
    """Add up the time that at least one of the intervals covers, counting overlaps once.

    :param intervals: Start and end of each interval, in any order.
    :type intervals: list[tuple[float, float]]
    :returns: The length of the union of the intervals.
    :rtype: float
    """
    total = 0.0
    covered_until: float | None = None
    for start, end in sorted(intervals):
        if covered_until is None or start > covered_until:
            total += end - start
            covered_until = end
        elif end > covered_until:
            total += end - covered_until
            covered_until = end
    return total


def percentile(values: list[float], percent: int) -> float:
    """Pick the nearest-rank percentile of the values.

    :param values: The values, at least one, in any order.
    :type values: list[float]
    :param percent: The percentile from 1 to 100.
    :type percent: int
    :returns: The smallest value that ``percent`` per cent of the values do not exceed.
    :rtype: float
    """
    ordered = sorted(values)
    return ordered[math.ceil(percent / PERCENT * len(ordered)) - 1]


def attribute_values(spans: list[Span], name: SpanName, key: SpanAttribute) -> list[float]:
    """Collect one numeric attribute from the spans of one name that recorded it.

    :param spans: The spans of the run.
    :type spans: list[Span]
    :param name: Name of the spans to read, such as ``write.block``.
    :type name: SpanName
    :param key: The attribute, such as the milliseconds of the wait for the lock.
    :type key: SpanAttribute
    :returns: The values, one for each span of the name that has the attribute, in the order of the file.
    :rtype: list[float]
    """
    return [float(span.attributes[key]) for span in spans if span.name == name and key in span.attributes]


def report(spans: list[Span], title: str) -> str:
    """Build the text of the report of a run.

    :param spans: The spans of the run, at least one.
    :type spans: list[Span]
    :param title: What the report is of, such as the name of the file.
    :type title: str
    :returns: The report as plain text, with the steps as a table.
    :rtype: str
    """
    wall = max(span.end for span in spans) - min(span.start for span in spans)
    blocks = [span for span in spans if span.name == SpanName.WRITE_BLOCK]
    # A block holds the lock from the end of its wait to its end; one that never took the lock held nothing
    holding = [
        (span.start + float(span.attributes.get(SpanAttribute.WAIT_MS, 0)) / MILLISECONDS_PER_SECOND, span.end)
        for span in blocks
        if span.attributes.get(SpanAttribute.BLOCK_OUTCOME) != BlockOutcome.NOT_BEGUN
    ]
    held = merged_length(holding)
    lines = [
        f'Trace report: {title}',
        f'Wall time of the run, s: {wall:.3f}',
        f'Write blocks: {len(blocks)}',
        f'Share of the run with the write lock held, %: {held / wall * PERCENT if wall else 0.0:{ONE_DECIMAL}}',
    ]
    for label, values in (
        ('Wait for the write lock, ms', attribute_values(spans, SpanName.WRITE_BLOCK, SpanAttribute.WAIT_MS)),
        ('Event-loop lag, ms', attribute_values(spans, SpanName.LOOP_LAG, SpanAttribute.LAG_MS)),
    ):
        if values:
            ranks = FIELD_SEPARATOR.join(f'p{rank} {percentile(values, rank):{TWO_DECIMALS}}' for rank in PERCENTILES)
            lines.append(f'{label}{LABEL_SEPARATOR}{ranks}, max {max(values):{TWO_DECIMALS}}')

    by_type: defaultdict[str, list[Span]] = defaultdict(list)
    for span in spans:
        if span.name == SpanName.STEP and SpanAttribute.STEP_TYPE in span.attributes:
            by_type[str(span.attributes[SpanAttribute.STEP_TYPE])].append(span)
    # The wait of the write blocks that the step span of the parent link encloses, summed for each step
    wait_by_parent: defaultdict[str, float] = defaultdict(float)
    for span in blocks:
        if span.parent_id is not None:
            wait_by_parent[span.parent_id] += float(span.attributes.get(SpanAttribute.WAIT_MS, 0))
    rows: list[tuple[str, ...]] = []
    for step_type, steps in sorted(by_type.items()):
        cached = sum(1 for step in steps if step.attributes.get(SpanAttribute.STEP_OUTCOME) == StepOutcome.CACHED)
        ran = [step for step in steps if step.attributes.get(SpanAttribute.STEP_OUTCOME) == StepOutcome.RAN]
        median = f'{statistics.median(step.duration_ms for step in ran):{ONE_DECIMAL}}' if ran else NO_VALUE
        median_wait = (
            f'{statistics.median(wait_by_parent.get(step.span_id, 0.0) for step in ran):{ONE_DECIMAL}}'
            if ran
            else NO_VALUE
        )
        share = f'{cached / len(steps) * PERCENT:{ONE_DECIMAL}}'
        rows.append((step_type, str(len(steps)), share, median, median_wait))
    if rows:
        widths = [max(len(row[column]) for row in (STEP_HEADER, *rows)) for column in range(len(STEP_HEADER))]
        lines.append('')
        for row in (STEP_HEADER, *rows):
            lines.append('| ' + ' | '.join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)) + ' |')
            if row is STEP_HEADER:
                lines.append('|' + '|'.join('-' * (width + 2) for width in widths) + '|')
    return '\n'.join(lines)


def check(spans: list[Span]) -> tuple[list[str], bool]:
    """Compare the run with the budgets.

    The preview budget is measured on the longest ``job`` span of the kind ``preview-step``: the whole preview job, with
    every step of it and the waits for the lock in them. The time the job waited in the queue before it started is not
    in the span, so it is not measured.

    :param spans: The spans of the run.
    :type spans: list[Span]
    :returns: One line for each budget, with its limit, the measured value and the verdict, and whether any budget is
        exceeded. A budget the run has nothing to measure for is ``no data`` and is not exceeded.
    :rtype: tuple[list[str], bool]
    """
    # The reader waits for the whole job, which runs every step up to the one they asked for, so the job is measured
    # and not its longest step. The wait in the queue before the job started is not part of its span
    previews = [
        span.duration_ms
        for span in spans
        if span.name == SpanName.JOB and span.attributes.get(SpanAttribute.JOB_KIND) == JobKind.PREVIEW_STEP
    ]
    waits = attribute_values(spans, SpanName.WRITE_BLOCK, SpanAttribute.WAIT_MS)
    measurements = (
        (PREVIEW_BUDGET, max(previews) if previews else None),
        (LOCK_WAIT_BUDGET, percentile(waits, LOCK_WAIT_PERCENT) if waits else None),
    )
    lines: list[str] = []
    exceeded = False
    for budget, measured in measurements:
        shown = NO_VALUE if measured is None else f'{measured:{TWO_DECIMALS}}'
        if measured is None:
            verdict = NO_DATA
        elif measured < budget.limit_ms:
            verdict = MET
        else:
            verdict = EXCEEDED
            exceeded = True
        fields = (f'limit {budget.limit_ms:{TWO_DECIMALS}}', f'measured {shown}', verdict)
        lines.append(f'{budget.name}{LABEL_SEPARATOR}{FIELD_SEPARATOR.join(fields)}')
    return lines, exceeded


def main(argv: list[str] | None = None) -> int:
    """Print the report of the file named on the command line, and with ``--check`` the verdicts on the budgets.

    :param argv: The arguments after the program name, or None to read them from the command line.
    :type argv: list[str] | None
    :returns: The exit status: 0 when the report was printed and no budget asked for is exceeded, 1 when the file holds
        no span or a budget is exceeded.
    :rtype: int
    """
    parser = argparse.ArgumentParser(
        prog='bookreviver-trace-report', description='Summarise the spans of one run of the backend.'
    )
    parser.add_argument('path', type=Path, help='the JSON lines file the measurements wrote')
    parser.add_argument(
        '--check', action='store_true', help='compare the run with the budgets and exit with 1 when one is exceeded'
    )
    arguments = parser.parse_args(argv)
    spans = load_spans(arguments.path)
    if not spans:
        sys.stderr.write(f'{arguments.path} holds no span.\n')
        return 1
    sys.stdout.write(report(spans, arguments.path.name) + '\n')
    if not arguments.check:
        return 0
    lines, exceeded = check(spans)
    sys.stdout.write('\n'.join(lines) + '\n')
    return 1 if exceeded else 0


if __name__ == '__main__':
    sys.exit(main())
