"""What a client shows of a stage of a book: the counts over its pages, one page's row, and the progress of a book.

The state of a stage of a page is stored in a ``PageStage`` record, and only once the stage has run on the page. A book
of a hundred pages has no records for a stage that never ran, so the summary of the stage counts them as not run from
the number of pages with an image. A stage done by hand, the import and the page order, has no records and no
processor. It has no counts either, and it is done as soon as the book has pages.

The status of a whole stage, the one the stage bar and the book list show, follows one rule: a stage that is not
available is unavailable, a stage a job is running is running, a stage with a stale, failed or marked page needs a look,
a stage every page of which is up to date is done, and any other stage waits. The next stage of a book is the first one
in the pipeline that has work to do.
"""

from typing import TYPE_CHECKING, Self

from attrs import evolve, field, frozen, validators

from bookreviver.domain.enums import PageStageStatus, StageStatus

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from bookreviver.domain.entities import PageVersion
    from bookreviver.domain.enums import ReviewReason, Stage
    from bookreviver.domain.ids import PageId, ProjectId, RecipeId

# The statuses of a stage that a user still has something to do or to watch in, which a book's next stage is taken from
STATUSES_WITH_WORK: frozenset[StageStatus] = frozenset(
    {StageStatus.ATTENTION, StageStatus.RUNNING, StageStatus.WAITING}
)


@frozen(kw_only=True)
class StageTally:
    """The pages of one stage of one book counted by the state of their records, as a repository counts them.

    :ivar project_id: Project owning the pages.
    :ivar stage: The stage.
    :ivar fresh: Pages whose current version of the stage matches its inputs.
    :ivar stale: Pages whose current version is out of date.
    :ivar failed: Pages the stage failed on.
    :ivar review: Pages, not failed, whose current version carries a review mark.
    :ivar check: Pages that are stale, failed or marked for review, each counted once.
    """

    project_id: ProjectId
    stage: Stage
    fresh: int = field(default=0, validator=validators.ge(0))
    stale: int = field(default=0, validator=validators.ge(0))
    failed: int = field(default=0, validator=validators.ge(0))
    review: int = field(default=0, validator=validators.ge(0))
    check: int = field(default=0, validator=validators.ge(0))


@frozen(kw_only=True)
class VariantTally:
    """The pages of one stage of one book that a recipe processed, as a repository counts them.

    :ivar stage: The stage.
    :ivar recipe_id: Recipe that processed the pages.
    :ivar pages: Pages with an image whose record of the stage names the recipe.
    """

    stage: Stage
    recipe_id: RecipeId
    pages: int = field(validator=validators.ge(1))


@frozen(kw_only=True)
class StageSummary:
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
    :ivar review: Pages, not failed, whose result carries a review mark.
    :ivar check: Pages the strip lists under Check: stale, failed or marked for review, each counted once.
    :ivar active_recipe_id: The recipe the stage runs by, or None when the stage has none yet, since the default recipes
                            are made the first time a stage is asked for.
    :ivar variants: How many pages each recipe of the stage processed, the recipe with the most pages first. A recipe
                    that processed none is left out, and so is the list of a book list, which does not read it.
    """

    stage: Stage
    available: bool
    manual: bool
    pages: int = field(validator=validators.ge(0))
    fresh: int = field(default=0, validator=validators.ge(0))
    stale: int = field(default=0, validator=validators.ge(0))
    failed: int = field(default=0, validator=validators.ge(0))
    not_run: int = field(default=0, validator=validators.ge(0))
    review: int = field(default=0, validator=validators.ge(0))
    check: int = field(default=0, validator=validators.ge(0))
    active_recipe_id: RecipeId | None = None
    variants: tuple[VariantTally, ...] = ()

    @classmethod
    def of(
        cls,
        stage: Stage,
        *,
        available: bool,
        pages: int,
        tally: StageTally | None,
        active_recipe_id: RecipeId | None,
    ) -> Self:
        """Sum a stage from the tally of its records.

        :param stage: The stage.
        :type stage: Stage
        :param available: Whether the stage can be worked in.
        :type available: bool
        :param pages: Number of pages of the book with an image.
        :type pages: int
        :param tally: Counts of the records of the stage, or None when no page has one.
        :type tally: StageTally | None
        :param active_recipe_id: The active recipe of the stage, or None.
        :type active_recipe_id: RecipeId | None
        :returns: The summary. A stage done by hand has no counts, whatever the tally says.
        :rtype: Self
        """
        if stage.manual:
            return cls(stage=stage, available=available, manual=True, pages=pages, active_recipe_id=active_recipe_id)
        fresh, stale, failed, review, check = (
            (0, 0, 0, 0, 0) if tally is None else (tally.fresh, tally.stale, tally.failed, tally.review, tally.check)
        )
        return cls(
            stage=stage,
            available=available,
            manual=False,
            pages=pages,
            fresh=fresh,
            stale=stale,
            failed=failed,
            not_run=max(0, pages - fresh - stale - failed),
            review=review,
            check=check,
            active_recipe_id=active_recipe_id,
        )

    def with_variants(self, variants: Sequence[VariantTally]) -> Self:
        """Add how many pages each recipe of the stage processed, the recipe with the most pages first.

        :param variants: The counts of the recipes of this stage.
        :type variants: Sequence[VariantTally]
        :returns: The summary with its variants. A stage done by hand has none, whatever the counts say.
        :rtype: Self
        """
        if self.manual:
            return self
        return evolve(self, variants=tuple(sorted(variants, key=lambda one: (-one.pages, str(one.recipe_id)))))

    def status(self, *, running: bool) -> StageStatus:
        """Give the status of the stage in the book.

        :param running: Whether a job that runs this stage is queued or running.
        :type running: bool
        :returns: The status by the rule of the module.
        :rtype: StageStatus
        """
        if not self.available:
            return StageStatus.UNAVAILABLE
        if self.manual:
            return StageStatus.DONE if self.pages else StageStatus.WAITING
        if running:
            return StageStatus.RUNNING
        if self.stale or self.failed or self.review:
            return StageStatus.ATTENTION
        if self.pages and not self.not_run:
            return StageStatus.DONE
        return StageStatus.WAITING


@frozen(kw_only=True)
class StageProgress:
    """The status of one stage of a book.

    :ivar stage: The stage.
    :ivar status: Where the stage stands.
    """

    stage: Stage
    status: StageStatus


@frozen(kw_only=True)
class BookProgress:
    """How far a book has come along the pipeline, one status for each stage.

    :ivar stages: The status of every stage, in the order of the pipeline.
    :ivar next_stage: The first available stage that has work to do or is being worked, or None when there is none.
    """

    stages: tuple[StageProgress, ...]
    next_stage: Stage | None = None

    @classmethod
    def of(cls, summaries: Sequence[StageSummary], running: Collection[Stage]) -> Self:
        """Sum a book up from the summaries of its stages.

        :param summaries: The summaries of the stages, in the order of the pipeline.
        :type summaries: Sequence[StageSummary]
        :param running: The stages a job of the book is running.
        :type running: Collection[Stage]
        :returns: The progress of the book.
        :rtype: Self
        """
        stages = tuple(
            StageProgress(stage=summary.stage, status=summary.status(running=summary.stage in running))
            for summary in summaries
        )
        next_stage = next((one.stage for one in stages if one.status in STATUSES_WITH_WORK), None)
        return cls(stages=stages, next_stage=next_stage)


@frozen(kw_only=True)
class StageRow:
    """One page of a book in one stage: its state there and the version that is its result.

    :ivar page_id: The page.
    :ivar status: The state of the stage on the page, or that the stage has not run on it.
    :ivar recipe_id: Recipe the page was processed by, or None.
    :ivar pinned: Whether the recipe is pinned to the page.
    :ivar head_version: The current version of the stage on the page, or None when there is none.
    """

    page_id: PageId
    status: PageStageStatus = PageStageStatus.NOT_RUN
    recipe_id: RecipeId | None = None
    pinned: bool = False
    head_version: PageVersion | None = None

    @property
    def review(self) -> ReviewReason | None:
        """Why the current version asks for a second look, or None when it does not or there is no version."""
        return None if self.head_version is None else self.head_version.review
