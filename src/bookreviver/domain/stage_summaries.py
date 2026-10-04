"""What a client shows of a stage of a book: the counts over its pages, one page's row, and the progress of a book.

The state of a stage of a page is stored in a ``PageStage`` record, and only once the stage has run on the page. A book
of a hundred pages has no records for a stage that never ran, so the summary of the stage counts them as not run from
the number of pages with an image. A stage done by hand, the import and the page order, has no records and no
processor. It has no counts either, and it is done as soon as the book has pages.

The status of a whole stage, the one the stage bar and the book list show, follows one rule: a stage that is not
available is unavailable, a stage a job is running is running, a stage with a stale, failed or marked page needs a look,
a stage every page of which is up to date and run through every step is done, and any other stage waits. The next
stage of a book is the first one in the pipeline that has work to do.
"""

from typing import TYPE_CHECKING, Self

from attrs import evolve, field, frozen, validators

from bookreviver.domain.enums import FigureState, PageStageStatus, ResultMark, StageStatus, VersionData

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from bookreviver.domain.entities import PageVersion, Recipe
    from bookreviver.domain.enums import ReviewReason, Stage
    from bookreviver.domain.ids import PageId, ProjectId, RecipeId, StepId

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
    :ivar partial: Pages, not failed, that were run through some of the steps of their recipe only.
    """

    project_id: ProjectId
    stage: Stage
    fresh: int = field(default=0, validator=validators.ge(0))
    stale: int = field(default=0, validator=validators.ge(0))
    failed: int = field(default=0, validator=validators.ge(0))
    review: int = field(default=0, validator=validators.ge(0))
    check: int = field(default=0, validator=validators.ge(0))
    partial: int = field(default=0, validator=validators.ge(0))


@frozen(kw_only=True)
class StepTally:
    """The pages of one stage of one book that a run stopped at one step of their recipe, as a repository counts them.

    :ivar stage: The stage.
    :ivar through_step: Index in the recipe of the last step the pages were run through.
    :ivar pages: Pages with an image, not failed, whose record of the stage stopped at that step.
    """

    stage: Stage
    through_step: int = field(validator=validators.ge(0))
    pages: int = field(validator=validators.ge(1))


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
    :ivar partial: Pages, not failed, that were run through some of the steps of their recipe only, so the stage after
                   this one cannot read them yet.
    :ivar active_recipe_id: The recipe the stage runs by, or None when the stage has none yet, since the default recipes
                            are made the first time a stage is asked for.
    :ivar variants: How many pages each recipe of the stage processed, the recipe with the most pages first. A recipe
                    that processed none is left out, and so is the list of a book list, which does not read it.
    :ivar stopped: How many pages stopped at each step, the first step first. The list of a book list leaves it out.
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
    partial: int = field(default=0, validator=validators.ge(0))
    active_recipe_id: RecipeId | None = None
    variants: tuple[VariantTally, ...] = ()
    stopped: tuple[StepTally, ...] = ()

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
        fresh, stale, failed, review, check, partial = (
            (0, 0, 0, 0, 0, 0)
            if tally is None
            else (tally.fresh, tally.stale, tally.failed, tally.review, tally.check, tally.partial)
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
            partial=partial,
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

    def with_stopped(self, stopped: Sequence[StepTally]) -> Self:
        """Add how many pages stopped at each step, the first step first.

        :param stopped: The counts of the steps of this stage.
        :type stopped: Sequence[StepTally]
        :returns: The summary with its steps. A stage done by hand has none, whatever the counts say.
        :rtype: Self
        """
        if self.manual:
            return self
        return evolve(self, stopped=tuple(sorted(stopped, key=lambda one: one.through_step)))

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
        if self.pages and not self.not_run and not self.partial:
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
class StepRow:
    """One page of a book at one step of a stage: what the step read, what it made and where its shape comes from.

    :ivar step_id: The step of the recipe the page was processed by.
    :ivar state: Where the shape of the step on the page comes from.
    :ivar input_version: The version the step reads on the page, which an editor of the step lies on, or None when the
                         page has not come as far as that, or the step is the first and the page is not run.
    :ivar version: The version the step made on the page, or None when the page was not run through the step, or its
                   recipe has no such step switched on.
    """

    step_id: StepId
    state: FigureState = FigureState.DEFAULT
    input_version: PageVersion | None = None
    version: PageVersion | None = None

    @classmethod
    def of(
        cls,
        step_id: StepId,
        recipe: Recipe | None,
        chain: Sequence[PageVersion],
        before: PageVersion | None,
        *,
        edited: bool,
    ) -> Self:
        """Place one step on one page from the versions that made the current version of the stage.

        The chain holds one version for each step that is on, so the place of a step in it is the number of steps that
        are on before it. A version of another processor than the step's stands for a step the recipe has changed since,
        which the page has not been through yet.

        :param step_id: The step.
        :type step_id: StepId
        :param recipe: The recipe the page was processed by, or None for a page no recipe processed.
        :type recipe: Recipe | None
        :param chain: The versions of the stage that made its current version, the first step first, or none.
        :type chain: Sequence[PageVersion]
        :param before: The version the first of the chain read, which an earlier stage made, or None.
        :type before: PageVersion | None
        :param edited: Whether the user made an edit of the step on the page.
        :type edited: bool
        :returns: The row. A page whose recipe has no such step, or has it switched off, has neither version.
        :rtype: Self
        """
        steps = () if recipe is None else recipe.enabled_steps
        place = None if recipe is None else recipe.place_of(step_id)
        made = None
        read = None
        if place is not None:
            if place < len(chain) and chain[place].processor.key == steps[place].processor_key:
                made = chain[place]
            read = before if place == 0 else (chain[place - 1] if place <= len(chain) else None)
        if made is not None and made.data.get(VersionData.SKIPPED_BY_CONDITION) is True:
            state = FigureState.SKIPPED
        elif edited:
            state = FigureState.BY_HAND
        else:
            state = FigureState.DEFAULT if made is None else FigureState.FOUND
        return cls(step_id=step_id, state=state, input_version=read, version=made)


@frozen(kw_only=True)
class StageRow:
    """One page of a book in one stage: its state there and the version that is its result.

    :ivar page_id: The page.
    :ivar status: The state of the stage on the page, or that the stage has not run on it.
    :ivar recipe_id: Recipe the page was processed by, or None.
    :ivar pinned: Whether the recipe is pinned to the page.
    :ivar head_version: The current version of the stage on the page, or None when there is none.
    :ivar through_step: Index in the recipe of the last step the page was run through when that is before the last step
                        that is on, or None.
    :ivar review_processor: Key of the processor of the first step of the stage that marked the page for review, or None
                            when the page is not marked or an earlier stage marked it.
    :ivar step: The page at the step the row was asked for, or None for a row of the stage as a whole.
    """

    page_id: PageId
    status: PageStageStatus = PageStageStatus.NOT_RUN
    recipe_id: RecipeId | None = None
    pinned: bool = False
    head_version: PageVersion | None = None
    through_step: int | None = None
    review_processor: str | None = None
    step: StepRow | None = None

    @property
    def marked_bad(self) -> bool:
        """Whether the result the row stands on is marked bad: the version at its step, else the current version."""
        shown = self.head_version if self.step is None else self.step.version
        return shown is not None and shown.mark is ResultMark.BAD

    @property
    def review(self) -> ReviewReason | None:
        """Why the current version asks for a second look, or None when it does not or there is no version."""
        return None if self.head_version is None else self.head_version.review
