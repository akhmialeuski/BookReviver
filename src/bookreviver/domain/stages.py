"""The stages of the digitisation pipeline, and where each page and the whole book stand in them."""

from typing import TYPE_CHECKING

from attrs import field, frozen, validators

from bookreviver.domain.labels import LabeledStrEnum

if TYPE_CHECKING:
    from datetime import datetime

    from bookreviver.domain.ids import PageId, PageVersionId, RecipeId


class Stage(LabeledStrEnum):
    """A step of the digitisation pipeline, in pipeline order."""

    IMPORT = 'import', 'Import'
    PAGE_SPLIT = 'page-split', 'Page split'
    PAGE_ORDER = 'page-order', 'Page order'
    GEOMETRY = 'geometry', 'Geometry'
    CLEANUP = 'cleanup', 'Cleanup'
    LAYOUT = 'layout', 'Layout'
    BACKGROUND = 'background', 'Background'
    RECOGNITION = 'recognition', 'Recognition'
    PROOFREADING = 'proofreading', 'Proofreading'
    TYPESETTING = 'typesetting', 'Typesetting'

    @property
    def position(self) -> int:
        """The place of the stage in the pipeline from zero, by which a stage is earlier or later than another."""
        return list(type(self)).index(self)

    @property
    def earlier(self) -> tuple[Stage, ...]:
        """The stages before this one, the nearest first, which is the order a stage looks for its input in."""
        return tuple(reversed(list(type(self))[: self.position]))

    @property
    def manual(self) -> bool:
        """Whether the user does the stage by hand, so it is available whatever plugins are installed."""
        return self in {Stage.IMPORT, Stage.PAGE_ORDER}


class StageState(LabeledStrEnum):
    """Whether the current version of a stage of a page still matches the inputs the stage would run on."""

    FRESH = 'fresh', 'Up to date'
    STALE = 'stale', 'Out of date'
    FAILED = 'failed', 'Failed'


class PageStageStatus(LabeledStrEnum):
    """Where one page stands in one stage: the state of its record, or that the stage has not run on it yet."""

    NOT_RUN = 'not-run', 'Not processed'
    FRESH = 'fresh', 'Up to date'
    STALE = 'stale', 'Out of date'
    FAILED = 'failed', 'Failed'

    @classmethod
    def of(cls, state: StageState | None) -> PageStageStatus:
        """Give the status of a page from the state of its record of a stage.

        :param state: State of the record, or None for a page the stage has no record of.
        :type state: StageState | None
        :returns: The status, which is not run for a page without a record.
        :rtype: PageStageStatus
        """
        return cls.NOT_RUN if state is None else cls(state.value)


class FigureState(LabeledStrEnum):
    """Where the shape of one step on one page comes from, which the step bar paints as a dot.

    A page that was never run through the step holds the default shape, which computes nothing. A step that ran leaves
    the shape it found, a manual edit puts the shape the user set and outlives the next run, and a leaf the program drew
    passes the step with no shape at all.
    """

    DEFAULT = 'default', 'Default shape'
    FOUND = 'found', 'Found by the step'
    BY_HAND = 'by-hand', 'Set by hand'
    SKIPPED = 'skipped', 'Skipped: the page is a leaf the program drew'


class StepFlag(LabeledStrEnum):
    """Why a page asks for a look at one step of a stage, which the strip of an open step narrows its pages by.

    A page can carry several. ``SKIPPED`` repeats the state of the shape, so a client filters by the flags alone.
    """

    UNSURE = 'unsure', 'The step was not sure of its result'
    UNUSUAL = 'unusual', 'What the step found differs notably from the rest of the book'
    BY_HAND = 'by-hand', 'Set by hand: a setting of the page or a shape the user drew'
    SKIPPED = 'skipped', 'Skipped: the page is a leaf the program drew'


class StageStatus(LabeledStrEnum):
    """Where a whole stage stands in a book, summed over its pages."""

    DONE = 'done', 'Done'
    ATTENTION = 'attention', 'Needs a look'
    RUNNING = 'running', 'Running'
    WAITING = 'waiting', 'Waiting'
    UNAVAILABLE = 'unavailable', 'Not available yet'


class ReviewReason(LabeledStrEnum):
    """Why a processed page is marked for a second look, though its step finished without an error."""

    LOW_CONFIDENCE = 'low-confidence', 'The step was not sure of its result'
    NOT_APPLIED = 'not-applied', 'The step left the page as it was, because it was not sure'
    UNSURE_GUTTER = 'unsure-gutter', 'The gutter of the spread was not found for certain'
    NARROW_GUTTER = 'narrow-gutter', 'Narrow scan with a gutter in the middle'
    CUT_BY_EDGE = 'cut-by-edge', 'Text may be cut by the edge of the scan'
    TEXT_SIZE = 'size-differs', 'The text of this page differs too much in size'
    FEW_LINES = 'few-lines', 'Too few lines of text were found to tell how the page is bent'
    HIGH_RESIDUAL = 'high-residual', 'The lines of the page are still bent after dewarping'


@frozen
class PageStageKey:
    """The key of the record of a stage of a page, which a repository takes as one value.

    :ivar page_id: Page the stage belongs to.
    :ivar stage: The stage.
    """

    page_id: PageId
    stage: Stage


@frozen(kw_only=True)
class PageStage:
    """The current version of one stage of one page, and whether it still matches the stage's inputs.

    The record keeps the last version of the recipe the page was processed by, so a version knows nothing of whether it
    is current. A change of an earlier stage marks the later ones stale and deletes none of their versions, so the
    interface shows the old result until it is recomputed.

    :ivar page_id: Page the record belongs to.
    :ivar stage: The stage.
    :ivar recipe_id: Recipe the page was processed by, or None when the recipe was deleted or none ran yet.
    :ivar head_version_id: Last version of the recipe's steps, which is the current version of the stage, or None.
    :ivar state: Whether the current version matches the inputs of the stage.
    :ivar through_step: Index in the recipe of the last step the page was run through, when that is before the last
                        step that is on, so the page is not ready for the next stage. None for a page run through every
                        step that is on, and for a record no recipe made.
    :ivar updated_at: When the record last changed.
    """

    page_id: PageId
    stage: Stage
    recipe_id: RecipeId | None = None
    head_version_id: PageVersionId | None = None
    state: StageState = StageState.FRESH
    through_step: int | None = field(default=None, validator=validators.optional(validators.ge(0)))
    updated_at: datetime

    @property
    def key(self) -> PageStageKey:
        """The key the record is stored under."""
        return PageStageKey(self.page_id, self.stage)
