"""Resetting the steps of a stage to their defaults, on one page or on every page of the book.

A step starts from the values of its recipe. A page changes it in two layers that this module takes away: the fields the
page changed for the step (``StepLayer.SETTINGS``) and the manual edit the step reads on the page (``StepLayer.HAND``).
What the automatic run found is not a layer of the state of a page yet, and the next run of the stage finds it again, so
a reset marks the stage of each page that lost something stale and processes nothing. The recipe is the default, so a
reset leaves it as it is.

The scopes are the step on the open page, every step of the stage on the open page, the step on every page, and every
step of the stage on every page. The steps of a stage are those of its recipes, the active one and the variants, a step
copied into a variant keeping its identifier. A reset that reaches pages other than the open one counts what it takes
and is refused until the user confirmed it, like a run that takes work away. All the changes are one batch of the
history, from a reset, so one undo gives the work back on every page.
"""

from typing import TYPE_CHECKING

from bookreviver.domain.enums import ChangeSource, StepLayer
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.history import StepReset
from bookreviver.domain.values import ResetImpact
from bookreviver.services.page_batches import PageBatch
from bookreviver.services.projects import book_pages, owned_page, owned_project

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.entities import Actor, PageStage, PageStepState
    from bookreviver.domain.ids import ProjectId, StepId
    from bookreviver.domain.values import ResetRequest
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.runtime import Clock
    from bookreviver.services.stage_records import StageRecords

RESET_NEEDS_CONFIRMATION: str = (
    'The reset {scope} takes the work of {pages} pages away, which it does only when it is confirmed.'
)
# The layers a page changes for a step, which a reset empties, in the order the history of the page records them
RESET_LAYERS: tuple[StepLayer, ...] = (StepLayer.SETTINGS, StepLayer.HAND)


class StepResetService:
    """Counts and does the resets of the steps of the acting account's projects."""

    def __init__(self, *, uow: UnitOfWork, records: StageRecords, clock: Clock) -> None:
        """Work over the ports of one request.

        :param uow: Unit of work of the request, whose commit ends the reset.
        :type uow: UnitOfWork
        :param records: Writer of the stage records, which a reset marks stale.
        :type records: StageRecords
        :param clock: Clock stamping the states and the changes.
        :type clock: Clock
        """
        self._uow = uow
        self._records = records
        self._clock = clock

    async def impact(self, actor: Actor, project_id: ProjectId, request: ResetRequest) -> ResetImpact:
        """Count the pages a reset would take work from, which is what a reset asks the user to confirm.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param request: The reset.
        :type request: ResetRequest
        :returns: How many pages have an edit and how many have settings on the steps the reset goes over, and how many
                  of them lose work.
        :rtype: ResetImpact
        :raises NotFoundError: If the actor has no such project, the project has no such page, or no recipe of the stage
                               has the step.
        """
        return ResetImpact.of(request.scope, await self._states(actor, project_id, request))

    async def reset(self, actor: Actor, project_id: ProjectId, request: ResetRequest) -> StepReset:
        """Take the settings and the edits of the pages away from the steps the reset goes over, as one batch.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param request: The reset.
        :type request: ResetRequest
        :returns: The batch and the changes written, one for each layer a page lost, which are none when none had any.
        :rtype: StepReset
        :raises NotFoundError: If the actor has no such project, the project has no such page, or no recipe of the stage
                               has the step.
        :raises ConflictError: If the reset reaches other pages than the open one, takes work from some of them and is
                               not confirmed.
        """
        states = await self._states(actor, project_id, request)
        affected = ResetImpact.of(request.scope, states).affected
        if request.reaches_other_pages and affected and not request.confirm:
            raise ConflictError(RESET_NEEDS_CONFIRMATION.format(scope=request.scope.label.lower(), pages=affected))
        batch = PageBatch(uow=self._uow, source=ChangeSource.RESET, moment=self._clock.now())
        for state in states:
            current = state
            for layer in RESET_LAYERS:
                current = await batch.write(current, layer, None)
        changes = await batch.flush()
        stale: list[PageStage] = []
        for page_id in dict.fromkeys(change.page_id for change in changes):
            stale.extend(await self._records.mark_stale(page_id, request.stage))
        await self._uow.commit()
        await self._records.announce(project_id, stale)
        return StepReset(batch_id=batch.batch_id, changes=tuple(changes))

    async def _states(self, actor: Actor, project_id: ProjectId, request: ResetRequest) -> Sequence[PageStepState]:
        """Read the states the reset goes over, which are the stored ones of its steps on its pages.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param request: The reset.
        :type request: ResetRequest
        :returns: The states, which are none for a step and a page that hold neither a setting nor an edit.
        :rtype: Sequence[PageStepState]
        :raises NotFoundError: If the actor has no such project, the project has no such page, or no recipe of the stage
                               has the step.
        """
        page_id = request.open_page
        if page_id is None:
            await owned_project(self._uow.projects, actor, project_id)
        else:
            await owned_page(self._uow, actor, project_id, page_id)
        step_ids = await self._step_ids(project_id, request)
        stage, states = request.stage, self._uow.page_step_states
        if page_id is not None:
            return [state for state in await states.list_for_page(page_id, stage) if state.step_id in step_ids]
        page_ids = [page.id for page in await book_pages(self._uow.pages, project_id)]
        found: list[PageStepState] = []
        for step_id in step_ids:
            found.extend(await states.list_for_step(page_ids, stage, step_id))
        return found

    async def _step_ids(self, project_id: ProjectId, request: ResetRequest) -> list[StepId]:
        """Find the steps the reset goes over: the one it names, or every step of the recipes of the stage.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param request: The reset.
        :type request: ResetRequest
        :returns: The identifiers of the steps, in the order the recipes have them.
        :rtype: list[StepId]
        :raises NotFoundError: If the reset names a step that no recipe of the stage has.
        """
        recipes = await self._uow.recipes.list_for_stage(project_id, request.stage)
        known = list(dict.fromkeys(step.step_id for recipe in recipes for step in recipe.steps))
        chosen = request.chosen_step
        if chosen is None:
            return known
        if chosen not in known:
            raise NotFoundError(chosen)
        return [chosen]
