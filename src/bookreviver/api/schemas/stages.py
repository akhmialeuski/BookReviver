"""Schemas of the stages of a book as a whole: the summary of each stage, the row of a page in one, the progress.

The stage bar draws itself from the summaries, the strip of pages from the rows, and the book list from the progress,
so each comes from one request instead of one for each page. The row of a page carries the current version of the
stage whole, in the same schema as the versions of a page, so the strip shows the result of this very stage with the
same image paths, data and review mark as everywhere else.
"""

from typing import TYPE_CHECKING, Self

from bookreviver.api.schemas.base import ResponseModel
from bookreviver.api.schemas.processing import PageVersionSchema
from bookreviver.domain.enums import FigureState, PageStageStatus, ReviewReason, Stage, StageStatus, StepFlag
from bookreviver.domain.ids import PageId, RecipeId, StepId

if TYPE_CHECKING:
    from starlette.requests import Request

    from bookreviver.domain.ids import ProjectId
    from bookreviver.domain.stage_summaries import StageRow, StepRow


class VariantPagesSchema(ResponseModel):
    """How many pages of a stage one recipe processed.

    :ivar recipe_id: The recipe.
    :ivar pages: Pages with an image whose result of the stage the recipe made.
    """

    recipe_id: RecipeId
    pages: int


class StepPagesSchema(ResponseModel):
    """How many pages of a stage a run stopped at one step.

    :ivar through_step: Index in the recipe of the last step the pages were run through, from zero.
    :ivar pages: Pages with an image, not failed, that stopped at the step.
    """

    through_step: int
    pages: int


class StageSummarySchema(ResponseModel):
    """One stage of a book summed over its pages.

    :ivar stage: The stage.
    :ivar available: Whether the stage can be worked in: a stage done by hand always can, and any other when a
                     processor of the stage is installed.
    :ivar manual: Whether the user does the stage by hand, so it has no counts.
    :ivar pages: Number of pages of the book with an image, which a run of the stage goes over.
    :ivar fresh: Pages whose result of the stage is up to date.
    :ivar stale: Pages whose result is out of date.
    :ivar failed: Pages the stage failed on.
    :ivar not_run: Pages the stage has not run on.
    :ivar review: Pages, not failed, whose result asks for a second look.
    :ivar check: Pages the strip lists under Check: stale, failed or marked, each counted once.
    :ivar partial: Pages, not failed, that were run through some of the steps of their recipe only.
    :ivar active_recipe_id: The recipe the stage runs by, or None before the stage is first used.
    :ivar variants: How many pages each recipe of the stage processed, the recipe with the most pages first.
    :ivar stopped: How many pages stopped at each step, the first step first.
    """

    stage: Stage
    available: bool
    manual: bool
    pages: int
    fresh: int
    stale: int
    failed: int
    not_run: int
    review: int
    check: int
    partial: int
    active_recipe_id: RecipeId | None
    variants: list[VariantPagesSchema]
    stopped: list[StepPagesSchema]


class StepPageSchema(ResponseModel):
    """One page of a book at one step of a stage: what the step read and made, and where its shape comes from.

    :ivar step_id: The step of the recipe.
    :ivar state: Where the shape of the step on the page comes from: the default, found by the step, set by hand, or
                 skipped because the page does not meet the condition of the step.
    :ivar input_version: The version the step reads on the page, which the canvas of the step shows, or None when the
                         page has not come as far as the step or is not run.
    :ivar version: The version the step made on the page, or None when the page was not run through the step.
    :ivar flags: Why the page asks for a look at the step: the step is unsure of it, what it found departs from the
                 book, it is set by hand, or the condition skipped it. The strip of the open step lists pages by these.
    """

    step_id: StepId
    state: FigureState
    input_version: PageVersionSchema | None
    version: PageVersionSchema | None
    flags: list[StepFlag]

    @classmethod
    def of(cls, row: StepRow, project_id: ProjectId, request: Request) -> Self:
        """Build the schema of the row of a page at a step.

        :param row: The row.
        :type row: StepRow
        :param project_id: Project owning the page, whose keys place the files of the versions.
        :type project_id: ProjectId
        :param request: The request, whose application knows the route that serves the images.
        :type request: Request
        :returns: The schema.
        :rtype: Self
        """
        return cls(
            step_id=row.step_id,
            state=row.state,
            input_version=None
            if row.input_version is None
            else PageVersionSchema.of(row.input_version, project_id, request),
            version=None if row.version is None else PageVersionSchema.of(row.version, project_id, request),
            flags=list(row.flags),
        )


class StagePageSchema(ResponseModel):
    """One page of a book in one stage: where it stands there and the version that is its result.

    :ivar page_id: The page.
    :ivar status: The state of the stage on the page, or ``not-run`` when the stage has not run on it.
    :ivar review: Why the result asks for a second look, or None.
    :ivar recipe_id: Recipe the page was processed by, or None.
    :ivar pinned: Whether the recipe is pinned to the page.
    :ivar version: The current version of the stage on the page with its data and images, or None.
    :ivar through_step: Index in the recipe of the last step the page was run through when that is before the last step
                        that is on, so the page is not ready for the next stage, or None.
    :ivar review_processor: Key of the processor of the first step of this stage that marked the page, or None.
    :ivar step: The page at the step the list was asked for, or None for a list of the stage alone.
    :ivar marked_bad: Whether the user marked bad the result the row stands on: the version of the step the list was
                      asked for, else the current version of the stage.
    """

    page_id: PageId
    status: PageStageStatus
    review: ReviewReason | None
    recipe_id: RecipeId | None
    pinned: bool
    version: PageVersionSchema | None
    through_step: int | None
    review_processor: str | None
    step: StepPageSchema | None
    marked_bad: bool

    @classmethod
    def of(cls, row: StageRow, project_id: ProjectId, request: Request) -> Self:
        """Build the schema of the row of a page.

        :param row: The row.
        :type row: StageRow
        :param project_id: Project owning the page, whose keys place the files of the version.
        :type project_id: ProjectId
        :param request: The request, whose application knows the route that serves the images.
        :type request: Request
        :returns: The schema.
        :rtype: Self
        """
        version = None if row.head_version is None else PageVersionSchema.of(row.head_version, project_id, request)
        return cls(
            page_id=row.page_id,
            status=row.status,
            review=row.review,
            recipe_id=row.recipe_id,
            pinned=row.pinned,
            version=version,
            through_step=row.through_step,
            review_processor=row.review_processor,
            step=None if row.step is None else StepPageSchema.of(row.step, project_id, request),
            marked_bad=row.marked_bad,
        )


class StageProgressSchema(ResponseModel):
    """The status of one stage of a book in the book list.

    :ivar stage: The stage.
    :ivar status: Where the stage stands: done, needs a look, running, waiting, or not available yet.
    """

    stage: Stage
    status: StageStatus
