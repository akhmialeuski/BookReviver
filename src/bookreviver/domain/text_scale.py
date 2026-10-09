"""The factor a page is scaled by to bring the distance between its lines to the one of the book, and when it is not.

``geometry.normalize`` scales the content box of a page so that its lines stand as far apart as the target line height,
unless the page is too far from the target, which means the lines were not measured right: such a page is left at its
own size. The measure of the book sizes the page of the book by the boxes the step will give the pages, so it asks the
same question, and the answer is worked out here, once, for the two of them.
"""

# The change of size, in percent of the target line height, that a page may be brought through, as the step starts with
DEFAULT_MAX_SCALE_CHANGE: float = 25.0
PERCENT: float = 100.0


def scale_factor(measured: float, wanted: float, max_change: float) -> float | None:
    """Work out the factor that brings the line height of a box to the target, or tell that the page is left as it is.

    A line height that is farther from the target than ``max_change`` percent of the target is not scaled, and a line
    height exactly ``max_change`` percent away still is.

    :param measured: Distance between the lines of the box, in pixels, more than zero.
    :type measured: float
    :param wanted: The target distance between the lines, in pixels, more than zero.
    :type wanted: float
    :param max_change: How far the line height may be from the target, in percent of the target.
    :type max_change: float
    :returns: The factor to scale the box by, or None when the page is too far from the target to be scaled.
    :rtype: float | None
    """
    # How far the text of the page is from the size of the book, as a share of the size of the book
    if abs(measured - wanted) / wanted * PERCENT > max_change:
        return None
    return wanted / measured
