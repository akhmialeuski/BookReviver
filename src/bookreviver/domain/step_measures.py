"""Telling a page whose value at a step departs from the rest of the book.

A step such as the deskew finds a number on every page, the angle, and a page whose angle is far from what the book
mostly has is worth a look even though the step was sure of it. The rule lives here and nowhere else: a step names what
it finds with a ``StepMeasure`` in its spec, the version a step made carries the value in its data, and the strip only
filters by the flag the server computed.

The book is taken as it stands in the stage: the median of the readings of every page the step made a value on. With
fewer than ``MIN_READINGS`` pages there is no book to compare with, so no page departs.
"""

import statistics
from typing import TYPE_CHECKING

from bookreviver.domain.enums import StepMeasure, VersionData
from bookreviver.domain.geometry import Rect

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.values import MetadataMap

type Reading = tuple[float, ...]

# The fewest pages that make a book to compare a page with
MIN_READINGS: int = 3
# How far an angle may be from the median of the book before the page is listed, in degrees
ANGLE_TOLERANCE_DEGREES: float = 1.0
# How far the width or the height of a frame may be from the median of the book before the page is listed, as a share
FRAME_TOLERANCE_SHARE: float = 0.15


def read_measure(measure: StepMeasure, data: MetadataMap) -> Reading | None:
    """Read what a step found on a page from the data of the version it made.

    :param measure: What the step finds.
    :type measure: StepMeasure
    :param data: Data of the version the step made.
    :type data: MetadataMap
    :returns: The numbers the measure is made of, or None when the step left the page as it was or found nothing.
    :rtype: Reading | None
    """
    if data.get(VersionData.SKIPPED) is True:
        return None
    match measure:
        case StepMeasure.ANGLE:
            angle = data.get(VersionData.ANGLE)
            return (float(angle),) if isinstance(angle, int | float) else None
        case StepMeasure.FRAME_SIZE:
            frame = data.get(VersionData.FRAME)
            if not isinstance(frame, dict):
                return None
            rect = Rect.from_data(frame)
            return (rect.width, rect.height)


def median_of(readings: Sequence[Reading]) -> Reading | None:
    """Take the median of each number of the readings of a book.

    :param readings: The readings of the pages the step made a value on.
    :type readings: Sequence[Reading]
    :returns: The median of each number, or None when there are too few pages to make a book.
    :rtype: Reading | None
    """
    if len(readings) < MIN_READINGS:
        return None
    medians: Reading = tuple(float(statistics.median(column)) for column in zip(*readings, strict=True))
    return medians


def departs(measure: StepMeasure, reading: Reading, median: Reading) -> bool:
    """Tell whether a page departs from the book.

    :param measure: What the step finds.
    :type measure: StepMeasure
    :param reading: What the step found on the page.
    :type reading: Reading
    :param median: The median of the book.
    :type median: Reading
    :returns: True when an angle is more than the tolerance in degrees from the median, or a side of the frame is more
              than the tolerance share of the median away from it.
    :rtype: bool
    """
    match measure:
        case StepMeasure.ANGLE:
            return abs(reading[0] - median[0]) > ANGLE_TOLERANCE_DEGREES
        case StepMeasure.FRAME_SIZE:
            return any(
                abs(value - typical) > FRAME_TOLERANCE_SHARE * typical
                for value, typical in zip(reading, median, strict=True)
            )
