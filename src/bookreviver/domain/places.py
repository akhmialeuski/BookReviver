"""Where a reader left a book, and how the workspace showed it."""

from typing import TYPE_CHECKING

from attrs import field, frozen, validators

from bookreviver.domain.labels import LabeledStrEnum
from bookreviver.domain.pages import PageFilter

if TYPE_CHECKING:
    from datetime import datetime

    from bookreviver.domain.ids import PageId, ScanId, SourceId
    from bookreviver.domain.stages import Stage


class PlaceMode(LabeledStrEnum):
    """Where in the interface a reader left a book."""

    WORKSPACE = 'workspace', 'Workspace of a stage'
    READING = 'reading', 'Reading mode'


class ViewMode(LabeledStrEnum):
    """How the canvas lays the pages of a book out."""

    PAGE = 'page', 'One page'
    SPREAD = 'spread', 'Two-page spread'
    GRID = 'grid', 'Grid of many pages'


class CompareMode(LabeledStrEnum):
    """How the result of a stage is compared with the result before it."""

    OFF = 'off', 'No comparison'
    SWIPE = 'swipe', 'Swipe over the page'
    SIDE = 'side', 'Side by side'


@frozen(kw_only=True)
class CanvasPosition:
    """Where the canvas looks at a view, in terms that do not depend on the size of the window.

    The zoom is a multiple of the zoom that fits the whole view into the canvas, so 1 is the fitted view and 2 shows
    half of it across. The centre is a point of the view in page heights, measured from its top left corner, so the
    same point is the centre on a phone and on a wide screen.

    :ivar zoom: Multiple of the zoom that fits the view, above zero.
    :ivar centre_x: Horizontal coordinate of the centre in page heights.
    :ivar centre_y: Vertical coordinate of the centre in page heights.
    """

    zoom: float = field(validator=validators.gt(0))
    centre_x: float
    centre_y: float


@frozen(kw_only=True)
class NewBookPlace:
    """Where a reader left a book, as the reader reports it; the time is added on storing.

    A workspace place names a stage and the page, or on the stages that work on files the file and the scan, the
    reader had open. A reading place names the page of the viewer, and keeps the stage the reader was working on, so
    the way back from reading is known. Pages, scans and files are kept by identifier and are not checked, since the
    reader may delete one afterwards and the place then falls back to the nearest view that still exists.

    :ivar mode: Whether the reader was working on a stage or reading.
    :ivar stage: Stage the reader was on, or last was on before reading.
    :ivar page_id: Page that was open, or None for the first page.
    :ivar scan_id: Scan that was open on the stages that work on scans, or None.
    :ivar source_id: File that was chosen on the stages that work on files, or None.
    :ivar view: How the canvas laid the pages out; the spread is the viewer's two-page spread as well.
    :ivar compare: How the result of the stage was compared with the one before it.
    :ivar filter: Which pages the strip or the grid listed.
    :ivar canvas: Zoom and centre of the canvas, or None for the fitted view.
    :ivar strip_page_id: First page in sight in the strip or the grid, or None for the top.
    """

    mode: PlaceMode
    stage: Stage
    page_id: PageId | None = None
    scan_id: ScanId | None = None
    source_id: SourceId | None = None
    view: ViewMode = ViewMode.PAGE
    compare: CompareMode = CompareMode.OFF
    filter: PageFilter = PageFilter.ALL
    canvas: CanvasPosition | None = None
    strip_page_id: PageId | None = None


@frozen(kw_only=True)
class BookPlace(NewBookPlace):
    """Where the reader left the book: the place the reader reported, and the time it was stored.

    A book has one place, which is replaced whole each time the reader moves, and read when the book is opened again, on
    any device. Its fields are those of :class:`NewBookPlace`.

    :ivar updated_at: When the place was last written.
    """

    updated_at: datetime
