"""The pages and recipes a run of a stage goes over, and what its mode takes away from them.

A run goes over the pages it names, or over every page of the book that has an image, and a page is run by the recipe
of its kind. Of the steps of that recipe a run goes over the ones that are on up to the step it stops at.

A run keeps the settings the pages changed for those steps and the manual edits the steps read, unless its mode says
otherwise. ``RunMode.DROP_OWN`` removes both, so the steps run with the values of the recipe and the automatic run finds
the shape again. ``RunMode.SKIP_OWN`` takes nothing away and leaves the pages that have either out of the run. The
request counts the pages that lose work and refuses the run until the user confirmed it, and the job takes the work away
before the first page is run. Every layer removed is a change of the history of its page, from a run, and all of them
are one batch, so one undo gives the work back on every page.
"""

from typing import TYPE_CHECKING

from bookreviver.domain.enums import ChangeSource, PageOrigin, RunMode
from bookreviver.domain.values import RunImpact
from bookreviver.services.page_batches import PageBatch
from bookreviver.services.projects import book_pages, owned_project

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.entities import Actor, Page, PageStepChange, PageStepState, Recipe
    from bookreviver.domain.ids import PageId, ProjectId, StepId
    from bookreviver.domain.values import StageRun
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.runtime import Clock
    from bookreviver.services.recipes import RecipeBook


class RunPlan:
    """The pages of one run, the recipe of each, and what the mode of the run does to the work of the pages."""

    def __init__(
        self, *, uow: UnitOfWork, recipes: RecipeBook, clock: Clock, project_id: ProjectId, run: StageRun
    ) -> None:
        """Plan one run over the ports of a request or a job.

        :param uow: Unit of work the pages, the recipes and the states are read through.
        :type uow: UnitOfWork
        :param recipes: The recipes of the project, which supply the recipe of each page.
        :type recipes: RecipeBook
        :param clock: Clock stamping the changes a mode writes.
        :type clock: Clock
        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param run: What the run was asked to do.
        :type run: StageRun
        """
        self._uow = uow
        self._recipes = recipes
        self._clock = clock
        self._project_id = project_id
        self._run = run
        self._named_pages: Sequence[Page] | None = None
        self._pages: Sequence[Page] | None = None
        self._by_page: dict[PageId, Recipe] | None = None

    async def pages(self) -> Sequence[Page]:
        """Choose the pages a run goes over: the ones it names, or every page of the book that has an image.

        A run that leaves out the pages with work of their own goes over the others.

        :returns: The pages in book order, without the placeholders, which have no image.
        :rtype: Sequence[Page]
        :raises NotFoundError: If a page the run names is not one of the project.
        """
        if self._pages is None:
            found = await self._named()
            if self._run.mode is RunMode.SKIP_OWN:
                own = self._own_ids(await self._states())
                found = [page for page in found if page.id not in own]
            self._pages = found
        return self._pages

    async def recipes(self) -> dict[PageId, Recipe]:
        """Choose the recipe of every page of the run.

        :returns: The recipe of each page, by page identifier.
        :rtype: dict[PageId, Recipe]
        :raises NotFoundError: If the stage has no recipe.
        """
        picked = await self._picked()
        return {page.id: picked[page.id] for page in await self.pages()}

    async def impact(self) -> RunImpact:
        """Count the pages that would lose work to the mode of the run.

        :returns: The pages the run goes over, and how many of the pages it names have an edit or change a field on the
                  steps the run goes over. A run that leaves out the pages with work of their own counts them before it
                  leaves them out.
        :rtype: RunImpact
        """
        states = await self._states()
        return RunImpact(
            mode=self._run.mode,
            pages=len(await self.pages()),
            own_pages=len(self._own_ids(states)),
        )

    async def apply_mode(self) -> Sequence[PageStepChange]:
        """Take the work the mode names away from the steps of the run on its pages, as one batch, and commit.

        A run that keeps the work changes nothing. The stage of a page is not marked stale, since the run that follows
        makes it again.

        :returns: The changes written, one for each layer removed, which are none when no page had any.
        :rtype: Sequence[PageStepChange]
        """
        if self._run.mode is not RunMode.DROP_OWN:
            return ()
        batch = PageBatch(uow=self._uow, source=ChangeSource.RUN, moment=self._clock.now())
        for state in await self._states():
            await batch.clear_work(state)
        written = await batch.flush()
        await self._uow.commit()
        return written

    async def _named(self) -> Sequence[Page]:
        """Choose the pages the run names, or every page of the book that has an image, before any is left out.

        :returns: The pages in book order, without the placeholders, which have no image.
        :rtype: Sequence[Page]
        :raises NotFoundError: If a page the run names is not one of the project.
        """
        if self._named_pages is None:
            if self._run.page_ids is None:
                found = await book_pages(self._uow.pages, self._project_id)
            else:
                found = list(await self._uow.pages.list_by_ids(self._project_id, self._run.page_ids))
            self._named_pages = [page for page in found if page.origin is not PageOrigin.PLACEHOLDER]
        return self._named_pages

    async def _picked(self) -> dict[PageId, Recipe]:
        """Choose the recipe of every page the run names, before any is left out.

        :returns: The recipe of each page, by page identifier.
        :rtype: dict[PageId, Recipe]
        :raises NotFoundError: If the stage has no recipe.
        """
        if self._by_page is None:
            self._by_page = await self._recipes.for_pages(self._project_id, self._run.stage, await self._named())
        return self._by_page

    @staticmethod
    def _own_ids(states: Sequence[PageStepState]) -> set[PageId]:
        """Name the pages that have work of their own in the states: an edit, or a field they changed.

        :param states: The states of the steps of the run.
        :type states: Sequence[PageStepState]
        :returns: The identifiers of the pages.
        :rtype: set[PageId]
        """
        return {state.page_id for state in states if state.edit is not None or state.params}

    async def _states(self) -> list[PageStepState]:
        """Read the states of the steps the run goes over, each step on the pages the run names with it.

        :returns: The states that are stored, which are none for a step and a page without settings and edit.
        :rtype: list[PageStepState]
        """
        by_step: dict[StepId, list[PageId]] = {}
        for page_id, recipe in (await self._picked()).items():
            for _, step in recipe.indexed_steps_through(self._run.through_step):
                by_step.setdefault(step.step_id, []).append(page_id)
        states: list[PageStepState] = []
        for step_id, page_ids in by_step.items():
            states.extend(await self._uow.page_step_states.list_for_step(page_ids, self._run.stage, step_id))
        return states


class RunImpactService:
    """Counts the pages a run would take work from, for the user to confirm before the run is sent."""

    def __init__(self, *, uow: UnitOfWork, recipes: RecipeBook, clock: Clock) -> None:
        """Count over the ports of one request.

        :param uow: Unit of work of the request.
        :type uow: UnitOfWork
        :param recipes: The recipes of the project.
        :type recipes: RecipeBook
        :param clock: Clock the plan of the run is built with.
        :type clock: Clock
        """
        self._uow = uow
        self._recipes = recipes
        self._clock = clock

    async def impact(self, actor: Actor, project_id: ProjectId, run: StageRun) -> RunImpact:
        """Count the pages a run would take work from, which is what a run asks the user to confirm.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param run: The run, with its mode.
        :type run: StageRun
        :returns: How many pages the run goes over, how many of them have settings and edits on its steps, and how many
                  lose work to its mode.
        :rtype: RunImpact
        :raises NotFoundError: If the actor has no such project or page, or the stage has no recipe.
        """
        await owned_project(self._uow.projects, actor, project_id)
        plan = RunPlan(uow=self._uow, recipes=self._recipes, clock=self._clock, project_id=project_id, run=run)
        return await plan.impact()
