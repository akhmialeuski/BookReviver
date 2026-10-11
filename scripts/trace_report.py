"""Summarise the spans of one run of the backend, written by its measurements as a JSON lines file.

Usage: ``uv run python scripts/trace_report.py PATH``

The file is the one ``BOOKREVIVER_TRACING__EXPORT_PATH`` names. It holds one OpenTelemetry span per line, and the
report is meant to be pasted into a note:

- the wall time of the run, from the start of the first span to the end of the last;
- how much of that time some write block held the write lock of the database, where blocks that overlap are counted
  once;
- the number of write blocks and the wait for the lock, as the 50th, 90th and 99th percentile and the maximum;
- for every type of step, how many steps there were, how many of them were found made by an earlier run, and the median
  milliseconds of those that ran.

A percentile is the nearest rank: the smallest value that at least that share of the values does not exceed.

The script reads only the file and uses the standard library only. The names of the spans and of their attributes are
written here as text on purpose, and ``tests/scripts/test_trace_report.py`` writes its fixture with the names the
application records, so a renamed span fails the test instead of emptying the report.
"""

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

WRITE_BLOCK: str = 'write.block'
STEP: str = 'step'
WAIT_MS: str = 'wait.ms'
BLOCK_OUTCOME: str = 'block.outcome'
STEP_TYPE: str = 'step.type'
STEP_OUTCOME: str = 'step.outcome'
# The outcome of a block that never took the lock, so it held nothing; and of a step an earlier run had made, and of one
# that ran
NOT_BEGUN: str = 'not-begun'
CACHED: str = 'cached'
RAN: str = 'ran'
MILLISECONDS_PER_SECOND: int = 1_000
PERCENT: int = 100
PERCENTILES: tuple[int, ...] = (50, 90, 99)
NO_VALUE: str = '-'
# Format specifications of the numbers of the report
ONE_DECIMAL: str = '.1f'
TWO_DECIMALS: str = '.2f'
STEP_HEADER: tuple[str, ...] = ('Step type', 'Count', 'Found cached, %', 'Median run, ms')


@dataclass(frozen=True)
class Span:
    """One span of the file.

    :ivar name: Name of the span, such as ``write.block``.
    :ivar start: Seconds since the epoch at which the span started.
    :ivar end: Seconds since the epoch at which the span ended.
    :ivar attributes: Facts the span carries.
    """

    name: str
    start: float
    end: float
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
            spans.append(Span(name=record['name'], start=start, end=end, attributes=record.get('attributes', {})))
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
    blocks = [span for span in spans if span.name == WRITE_BLOCK]
    # A block holds the lock from the end of its wait to its end; one that never took the lock held nothing
    holding = [
        (span.start + float(span.attributes.get(WAIT_MS, 0)) / MILLISECONDS_PER_SECOND, span.end)
        for span in blocks
        if span.attributes.get(BLOCK_OUTCOME) != NOT_BEGUN
    ]
    held = merged_length(holding)
    lines = [
        f'Trace report: {title}',
        f'Wall time of the run, s: {wall:.3f}',
        f'Write blocks: {len(blocks)}',
        f'Share of the run with the write lock held, %: {held / wall * PERCENT if wall else 0.0:{ONE_DECIMAL}}',
    ]
    waits = [float(span.attributes[WAIT_MS]) for span in blocks if WAIT_MS in span.attributes]
    if waits:
        ranks = ', '.join(f'p{rank} {percentile(waits, rank):{TWO_DECIMALS}}' for rank in PERCENTILES)
        lines.append(f'Wait for the write lock, ms: {ranks}, max {max(waits):{TWO_DECIMALS}}')

    by_type: defaultdict[str, list[Span]] = defaultdict(list)
    for span in spans:
        if span.name == STEP and STEP_TYPE in span.attributes:
            by_type[str(span.attributes[STEP_TYPE])].append(span)
    rows: list[tuple[str, ...]] = []
    for step_type, steps in sorted(by_type.items()):
        cached = sum(1 for step in steps if step.attributes.get(STEP_OUTCOME) == CACHED)
        runs = [step.duration_ms for step in steps if step.attributes.get(STEP_OUTCOME) == RAN]
        median = f'{statistics.median(runs):{ONE_DECIMAL}}' if runs else NO_VALUE
        rows.append((step_type, str(len(steps)), f'{cached / len(steps) * PERCENT:{ONE_DECIMAL}}', median))
    if rows:
        widths = [max(len(row[column]) for row in (STEP_HEADER, *rows)) for column in range(len(STEP_HEADER))]
        lines.append('')
        for row in (STEP_HEADER, *rows):
            lines.append('| ' + ' | '.join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)) + ' |')
            if row is STEP_HEADER:
                lines.append('|' + '|'.join('-' * (width + 2) for width in widths) + '|')
    return '\n'.join(lines)


def main(argv: list[str] | None = None) -> int:
    """Print the report of the file named on the command line.

    :param argv: The arguments after the program name, or None to read them from the command line.
    :type argv: list[str] | None
    :returns: The exit status: 0 when the report was printed, 1 when the file holds no span.
    :rtype: int
    """
    parser = argparse.ArgumentParser(description='Summarise the spans of one run of the backend.')
    parser.add_argument('path', type=Path, help='the JSON lines file the measurements wrote')
    arguments = parser.parse_args(argv)
    spans = load_spans(arguments.path)
    if not spans:
        sys.stderr.write(f'{arguments.path} holds no span.\n')
        return 1
    sys.stdout.write(report(spans, arguments.path.name) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
