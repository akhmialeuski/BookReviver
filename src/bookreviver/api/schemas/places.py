"""Schemas of the place an account left a book at: where the reader was, and the position of the canvas there.

The client sends the place whole after every move and reads it back when the book is opened, so the body and the answer
hold the same fields. The answer leaves out the account and the book, which the address names.
"""

from datetime import datetime

from bookreviver.api.schemas.base import RequestModel, ResponseModel
from bookreviver.api.schemas.types import CanvasCentre, CanvasZoom
from bookreviver.domain.enums import CompareMode, PageFilter, PlaceMode, Stage, ViewMode
from bookreviver.domain.ids import PageId, ScanId, SourceId
from bookreviver.domain.values import CanvasPosition, NewBookPlace


class CanvasPositionBody(RequestModel):
    """Where the canvas looks, as the client reports it.

    :ivar zoom: Multiple of the zoom that fits the whole view into the canvas, so 1 is the fitted view.
    :ivar centre_x: Horizontal coordinate of the centre in page heights, from the left edge of the view.
    :ivar centre_y: Vertical coordinate of the centre in page heights, from the top edge of the view.
    """

    zoom: CanvasZoom
    centre_x: CanvasCentre
    centre_y: CanvasCentre


class BookPlaceBody(RequestModel):
    """Where the reader is in the book, which replaces the place stored.

    :ivar mode: Whether the reader is working on a stage or reading.
    :ivar stage: Stage the reader is on, or last was on before reading.
    :ivar page_id: Page that is open, or omitted for the first page.
    :ivar scan_id: Scan that is open on the stages that work on scans, or omitted.
    :ivar source_id: File that is chosen on the stages that work on files, or omitted.
    :ivar view: How the canvas lays the pages out; the spread is the two-page spread of the reading mode as well.
    :ivar compare: How the result of the stage is compared with the one before it.
    :ivar filter: Which pages the strip or the grid lists.
    :ivar canvas: Zoom and centre of the canvas, or omitted for the fitted view.
    :ivar strip_page_id: First page in sight in the strip or the grid, or omitted for the top.
    """

    mode: PlaceMode
    stage: Stage
    page_id: PageId | None = None
    scan_id: ScanId | None = None
    source_id: SourceId | None = None
    view: ViewMode = ViewMode.PAGE
    compare: CompareMode = CompareMode.OFF
    filter: PageFilter = PageFilter.ALL
    canvas: CanvasPositionBody | None = None
    strip_page_id: PageId | None = None

    def to_place(self) -> NewBookPlace:
        """Return the place as the domain states it.

        :returns: The place without the account, the book and the time, which the use case adds.
        :rtype: NewBookPlace
        """
        canvas = self.canvas
        return NewBookPlace(
            mode=self.mode,
            stage=self.stage,
            page_id=self.page_id,
            scan_id=self.scan_id,
            source_id=self.source_id,
            view=self.view,
            compare=self.compare,
            filter=self.filter,
            canvas=None
            if canvas is None
            else CanvasPosition(zoom=canvas.zoom, centre_x=canvas.centre_x, centre_y=canvas.centre_y),
            strip_page_id=self.strip_page_id,
        )


class CanvasPositionSchema(ResponseModel):
    """Where the canvas looked.

    :ivar zoom: Multiple of the zoom that fits the whole view into the canvas.
    :ivar centre_x: Horizontal coordinate of the centre in page heights, from the left edge of the view.
    :ivar centre_y: Vertical coordinate of the centre in page heights, from the top edge of the view.
    """

    zoom: float
    centre_x: float
    centre_y: float


class BookPlaceSchema(ResponseModel):
    """Where the account left the book.

    :ivar mode: Whether the reader was working on a stage or reading.
    :ivar stage: Stage the reader was on, or last was on before reading.
    :ivar page_id: Page that was open, or None for the first page.
    :ivar scan_id: Scan that was open, or None.
    :ivar source_id: File that was chosen, or None.
    :ivar view: How the canvas laid the pages out.
    :ivar compare: How the result of the stage was compared with the one before it.
    :ivar filter: Which pages the strip or the grid listed.
    :ivar canvas: Zoom and centre of the canvas, or None for the fitted view.
    :ivar strip_page_id: First page in sight in the strip or the grid, or None for the top.
    :ivar updated_at: When the place was written.
    """

    mode: PlaceMode
    stage: Stage
    page_id: PageId | None
    scan_id: ScanId | None
    source_id: SourceId | None
    view: ViewMode
    compare: CompareMode
    filter: PageFilter
    canvas: CanvasPositionSchema | None
    strip_page_id: PageId | None
    updated_at: datetime
