"""Page versions, the results of steps on pages: their inputs, state and data, and the user's mark."""

import hashlib
import json
import re
from typing import TYPE_CHECKING, Any, Self

from attrs import field, frozen, validators

from bookreviver.domain.geometry import Transform
from bookreviver.domain.ids import PageVersionId
from bookreviver.domain.images import Renditions
from bookreviver.domain.labels import LabeledStrEnum

if TYPE_CHECKING:
    from datetime import datetime

    from bookreviver.domain.ids import PageId, StepId
    from bookreviver.domain.metadata import MetadataMap
    from bookreviver.domain.sides import PageSide
    from bookreviver.domain.stages import ReviewReason, Stage


class VersionData(LabeledStrEnum):
    """Keys of the data of a version: the size of its image, what its step found, and why it failed."""

    WIDTH_PX = 'width_px', 'Width of the image in pixels'
    HEIGHT_PX = 'height_px', 'Height of the image in pixels'
    DPI = 'dpi', 'Resolution of the image in dots per inch'
    ERROR = 'error', 'Why the version could not be made'
    ANGLE = 'angle', 'Angle a page was turned by, in degrees'
    CONFIDENCE = 'confidence', 'How sure the step is of what it found, from 0 to 1'
    SKIPPED = 'skipped', 'Whether the step left the image as it was'
    # The value is the key that versions stored while a step had a condition, which a leaf is skipped by now
    SKIPPED_LEAF = (
        'skipped_by_condition',
        'Whether the page is a leaf the program drew, which the step passed unchanged',
    )
    OVERLAP_PX = 'overlap_px', 'Width in pixels a half of a spread reaches over the cut'
    CUT_X = 'cut_x', 'Place of the cut in the scan, as the distance in pixels from its left edge'
    CUT_TOP_X = 'cut_top_x', 'Place of the cut at the top row of the scan, in pixels from its left edge'
    CUT_BOTTOM_X = 'cut_bottom_x', 'Place of the cut at the bottom row of the scan, in pixels from its left edge'
    PAGES = 'pages', 'Number of pages the scan was split into, one or two'
    SOURCE_WIDTH_PX = 'source_width_px', 'Width in pixels of the full image the edit of the step is drawn on'
    SOURCE_HEIGHT_PX = (
        'source_height_px',
        'Height in pixels of the full image the edit of the step is drawn on',
    )
    LINE_HEIGHT_PX = 'line_height_px', 'Median distance between the lines of text, in the pixels of the full image'
    QUAD = 'quad', 'Corners of the sheet the step found in its input, in the pixels of the full image'
    FRAME = 'frame', 'Frame of the content the step found in its input, in the pixels of the full image'
    CUT_EDGES = 'cut_edges', 'Sides of the sheet that lie on the edge of the scan, where the paper was cut'
    REVIEW = 'review', 'Reason an earlier step of the recipe marked the page for a second look'
    LINES = 'lines', 'Number of lines of text or edges of the sheet a dewarping followed'
    BEND = 'bend', 'How far the lines of the page were bent, in pixels for each thousand of its width'
    RESIDUAL = 'residual', 'How far the lines still deviate from straight after dewarping, in pixels per thousand'
    MESH = 'mesh', 'The nodes of the curves a dewarping followed, in the pixels of the full image, for its editor'
    CONTENT_FRAME = 'content_frame', 'Frame of the content in the pixels of the image of this version'
    METHOD = 'method', 'Method that parted the ink from the paper'
    MODE = 'mode', 'What the page was made into'
    THRESHOLD = 'threshold', 'Threshold of the page when the method has one for the whole page'
    ZONES = 'zones', 'Picture zones of the page in the pixels of the full image the step read'
    SPECKS = 'specks', 'Number of specks the step removed'
    CONTENT_TYPE = 'content_type', 'What the page shows: text, a colour picture or a black-and-white one'
    PICTURE_SHARE = 'picture_share', 'Share of the page that pictures cover, from 0 to 1'
    CONTENT_BOX = 'content_box', 'Content box the step placed on the page, in the pixels of the full image it read'
    BLOCK_SCALE = 'block_scale', 'Factor the step scaled the content box by when it placed it on the page'
    MARGIN_BOX = 'margin_box', 'Content box grown by the margins of the page, in the pixels of the full image read'
    MARGIN_PARAMS = (
        'margin_params',
        'Names of the parameters that hold the margin of each side of the page, which the side of the book decides',
    )
    MARGIN_PIXELS_PER_MM = (
        'margin_pixels_per_mm',
        'Pixels of the full image the step read in a millimetre of the margins of the page',
    )


class VersionState(LabeledStrEnum):
    """Lifecycle of a page version, from its creation to its result."""

    PENDING = 'pending', 'Pending'
    RUNNING = 'running', 'Running'
    READY = 'ready', 'Ready'
    FAILED = 'failed', 'Failed'


class VersionScale(LabeledStrEnum):
    """The size of the image a step ran on, which tells a full run from a preview of its parameters."""

    FULL = 'full', 'Full image'
    PREVIEW = 'preview', 'Preview image'


class ResultMark(LabeledStrEnum):
    """What the user judged of a result, a page version, which a result without a judgement does not have."""

    GOOD = 'good', 'Good'
    BAD = 'bad', 'Bad'


class VersionOrigin(LabeledStrEnum):
    """How a result, a page version, came about, which a version tells from the inputs it was made from."""

    AUTO = 'auto', 'Made by the step'
    HAND = 'hand', 'Set by hand'


@frozen(kw_only=True)
class ProcessorRef:
    """The processor that made a page version, by key and version.

    :ivar key: Key of the processor, such as ``geometry.deskew``, which also names its storage directory.
    :ivar version: Version of the processor, such as ``1.2``; a new version computes new page versions.
    """

    key: str = field(validator=validators.min_len(1))
    version: str = field(validator=validators.min_len(1))


@frozen(kw_only=True)
class PageSize:
    """The size of a page image in pixels and the resolution it was made at, as a base version records them.

    The median of these over the pages of a book gives the size of a generated blank leaf, so the leaf stands level
    with its neighbours in a spread.

    :ivar width_px: Width of the image in pixels.
    :ivar height_px: Height of the image in pixels.
    :ivar dpi: Resolution in dots per inch, or None when the page has none recorded.
    """

    width_px: int = field(validator=validators.gt(0))
    height_px: int = field(validator=validators.gt(0))
    dpi: float | None = field(default=None, validator=validators.optional(validators.gt(0)))

    @classmethod
    def from_data(cls, data: MetadataMap) -> Self | None:
        """Read the size a base version recorded in its data.

        :param data: Data of a page version.
        :type data: MetadataMap
        :returns: The size, or None when the data holds no usable width and height.
        :rtype: Self | None
        """
        width, height, dpi = (data.get(key) for key in (VersionData.WIDTH_PX, VersionData.HEIGHT_PX, VersionData.DPI))
        if not (isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0):
            return None
        return cls(width_px=width, height_px=height, dpi=dpi if isinstance(dpi, int | float) and dpi > 0 else None)

    def as_data(self) -> dict[str, Any]:
        """Return the size as the data of a base version, which leaves out an unknown resolution.

        :returns: The width and height, and the resolution when it is known.
        :rtype: dict[str, Any]
        """
        data: dict[str, Any] = {VersionData.WIDTH_PX: self.width_px, VersionData.HEIGHT_PX: self.height_px}
        if self.dpi is not None:
            data[VersionData.DPI] = self.dpi
        return data


@frozen(kw_only=True)
class VersionFilter:
    """Which versions of a page a listing returns.

    :ivar stage: Stage whose versions are listed, or None for every stage.
    :ivar scale: Scale of the runs listed, or None for both full runs and previews.
    :ivar step_id: Step of a recipe of the stage whose versions are listed, or None for the versions of every step. A
                   step belongs to a stage, so it comes with one.
    :ivar mark: Mark the versions carry, or None for every version, whether marked or not.
    """

    stage: Stage | None = None
    scale: VersionScale | None = None
    step_id: StepId | None = None
    mark: ResultMark | None = None


@frozen(kw_only=True)
class ResultNote:
    """What the user notes on a result: a mark and a comment, which are set and replaced together.

    :ivar mark: Good or bad, or None for a result without a mark.
    :ivar comment: The comment, one line or several, or empty for none.
    """

    mark: ResultMark | None = None
    comment: str = ''


@frozen(kw_only=True)
class CollectionReport:
    """What a collection would delete from a book if it ran now, which the book's owner reads before starting it.

    :ivar versions: Number of versions the collection would delete, with their rows and the log of their marks.
    :ivar size_bytes: Size of the files of those versions, which the collection would free.
    """

    versions: int = field(validator=validators.ge(0))
    size_bytes: int = field(validator=validators.ge(0))


# The length of a page version identifier: a hash cut to 16 hexadecimal digits
VERSION_ID_LENGTH: int = 16
VERSION_ID_PATTERN: str = rf'[0-9a-f]{{{VERSION_ID_LENGTH}}}'


@frozen(kw_only=True)
class VersionInputs:
    """Everything the result of one step on one page depends on, which the identifier of the version hashes.

    Equal inputs give equal results, so a repeated step finds the files of its earlier result, and an input that changes
    the image must change the identifier or the cache would return another result. What does not change the image, such
    as the time of the run or the recipe a step came from, is not an input, so one step with the same parameters in two
    recipes is one version.

    :ivar page_id: Page the step runs on. It is hashed because a base version has no input version, and two blank
                   leaves of one size would share an identifier without it.
    :ivar processor: Key and version of the processor running the step.
    :ivar params: Parameters of the step after validation, which must be JSON-compatible.
    :ivar input_id: Version the step reads, or None for a base version.
    :ivar edit_hash: Hash of the manual edit the step reads, or empty for none.
    :ivar scale: Whether the step runs on the full image or on the preview.
    :ivar side: Side of the book the page lies on, for a step that reads it, or None for a step that does not.
    :ivar skipped: Whether the page is a leaf the program drew and passes the step unchanged. Such a version
                   holds the image of its input whatever the parameters and the edit of the step are, so it has none.
    """

    page_id: PageId
    processor: ProcessorRef
    params: MetadataMap = field(factory=dict)
    input_id: PageVersionId | None = None
    edit_hash: str = ''
    scale: VersionScale = VersionScale.FULL
    side: PageSide | None = None
    skipped: bool = False

    def identify(self) -> PageVersionId:
        """Return the identifier of the version these inputs produce, a hash of all of them.

        The edit, the scale, the side and the skip join the hash only when they are not the empty edit, the full scale,
        no side and a page that is processed, so a full run without an edit hashes what it hashed before they existed,
        and the identifiers already stored stay the ones a repeated run finds.

        :returns: The SHA-256 of the inputs in canonical JSON, cut to 16 lower-case hexadecimal digits.
        :rtype: PageVersionId
        """
        produced_by = [str(self.page_id), self.processor.key, self.processor.version, self.params, self.input_id]
        if self.edit_hash:
            produced_by.append({'edit': self.edit_hash})
        if self.scale is not VersionScale.FULL:
            produced_by.append({'scale': self.scale.value})
        if self.side is not None:
            produced_by.append({'side': self.side.value})
        if self.skipped:
            produced_by.append({'skipped': True})
        digest = hashlib.sha256(json.dumps(produced_by, sort_keys=True, separators=(',', ':')).encode())
        return PageVersionId(digest.hexdigest()[:VERSION_ID_LENGTH])


@frozen(kw_only=True)
class PageVersion:
    """The result of one processing step on one page of the book, never changed once ready.

    A version is identified by the hash of its ``VersionInputs``, so equal work gets the same identifier and its cached
    result. The first version of a page, its base version,
    has no input version: its input is a scan or, for a blank leaf, nothing.

    :ivar id: Identifier of the version, the hash of what produced it.
    :ivar page_id: Page of the book the version belongs to.
    :ivar stage: Stage of the step.
    :ivar processor: Key and version of the processor that ran the step.
    :ivar input_id: Version the step read, or None for a base version.
    :ivar params: Parameters of the step, following the processor's JSON Schema.
    :ivar transform: Transform of coordinates from the input to this version.
    :ivar data: Data the step found, such as an angle, a frame or a confidence.
    :ivar review: Why the page is to be looked at again though the step finished, as the processor reported it, or None
                  when the step was sure of its result.
    :ivar renditions: State of the version's image files, or None for a step without an image.
    :ivar state: Where the version is in its lifecycle.
    :ivar scale: Whether the step ran on the full image or on the preview, which only the preview of a parameter shows.
    :ivar edit_hash: Hash of the manual edit the step read, or empty for a step without one.
    :ivar tiles_ready: Whether the IIIF tile pyramid of the version is cut, which is done for the current version of a
                       stage and for any other version on request.
    :ivar created_at: When the version was created.
    :ivar mark: What the user judged of the result, or None while it is not judged. The mark and the comment are the
                user's notes on the result, not an input of the step, so neither changes the identifier.
    :ivar comment: What the user wrote about the result, or empty.
    """

    id: PageVersionId
    page_id: PageId
    stage: Stage
    processor: ProcessorRef
    input_id: PageVersionId | None = None
    params: MetadataMap = field(factory=dict)
    transform: Transform = field(factory=Transform)
    data: MetadataMap = field(factory=dict)
    review: ReviewReason | None = None
    renditions: Renditions | None = field(factory=Renditions)
    state: VersionState = VersionState.PENDING
    scale: VersionScale = VersionScale.FULL
    edit_hash: str = ''
    tiles_ready: bool = False
    created_at: datetime
    mark: ResultMark | None = None
    comment: str = ''

    @property
    def origin(self) -> VersionOrigin:
        """How the result came about: set by hand when the step read a manual edit, else made by the step itself."""
        return VersionOrigin.HAND if self.edit_hash else VersionOrigin.AUTO

    def __attrs_post_init__(self) -> None:
        """Check the identifier and the renditions, which place the version's files in storage.

        :raises ValueError: If the identifier is not 16 lower-case hexadecimal digits, or the renditions are past their
                            first version, since a version is written once into the directory of its identifier.
        """
        if not re.fullmatch(VERSION_ID_PATTERN, self.id):
            err_msg = f'A page version id is 16 lower-case hexadecimal digits, not {self.id!r}.'
            raise ValueError(err_msg)
        if self.renditions is not None and self.renditions.version != Renditions.FIRST_VERSION:
            err_msg = 'The renditions of a page version are written once and stay at their first version.'
            raise ValueError(err_msg)
