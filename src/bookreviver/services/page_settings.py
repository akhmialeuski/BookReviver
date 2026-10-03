"""The settings a page has for a step of a recipe, which only that page uses.

A step of a recipe holds the parameters every page runs with. A page may change a single field of them, for example the
method of the deskew or the threshold of the binarization, and keeps only the fields it changed in its
``PageStepState``. A run lays them over the parameters of the step, so the other pages are untouched, a field changed in
the recipe reaches every page that did not change it, and a field the page changed keeps its value.

A value is checked as a field of the parameters of the processor of the step, together with the other fields the step
runs with on the page, so a field the processor does not have, or a value out of its range, is refused before anything
is stored. The value that is stored is the one the processor returns, which has its type made right.

Every change is written to the history of the page in the ``settings`` layer, with the whole layer before and after, and
marks the stage of the page stale, since the version it shows was made with other parameters. Nothing is computed: a run
of the stage picks the settings up like the parameters of the recipe.
"""

from typing import TYPE_CHECKING
from uuid import uuid4

from attrs import evolve

from bookreviver.domain.entities import PageStepChange, PageStepState
from bookreviver.domain.enums import ChangeSource, StepLayer
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import PageStepChangeId
from bookreviver.services.projects import owned_project
from bookreviver.services.recipes import find_step

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.entities import Actor
    from bookreviver.domain.enums import Stage
    from bookreviver.domain.ids import PageId, ProjectId
    from bookreviver.domain.values import MetadataMap, PageStepKey
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.processing import ProcessorCatalog
    from bookreviver.ports.runtime import Clock
    from bookreviver.services.stage_records import StageRecords


class PageSettingsService:
    """Changes, resets and lists the settings of the pages of the acting account's projects."""

    def __init__(self, *, uow: UnitOfWork, catalogue: ProcessorCatalog, records: StageRecords, clock: Clock) -> None:
        """Work over the ports of one request.

        :param uow: Unit of work of the request, whose commit ends every changing use case.
        :type uow: UnitOfWork
        :param catalogue: The processors the application can run, which check the value of a field.
        :type catalogue: ProcessorCatalog
        :param records: Writer of the stage records, which a changed setting marks stale.
        :type records: StageRecords
        :param clock: Clock stamping the states and the changes.
        :type clock: Clock
        """
        self._uow = uow
        self._catalogue = catalogue
        self._records = records
        self._clock = clock

    async def list(self, actor: Actor, project_id: ProjectId, page_id: PageId, stage: Stage) -> Sequence[PageStepState]:
        """List the steps of one stage of a page that have settings of the page, by step.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :param stage: The stage whose steps are listed.
        :type stage: Stage
        :returns: The states of the steps that change at least one field.
        :rtype: Sequence[PageStepState]
        :raises NotFoundError: If the actor has no such project, or the project has no such page.
        """
        await self._check_page(actor, project_id, page_id)
        return [state for state in await self._uow.page_step_states.list_for_page(page_id, stage) if state.params]

    async def change(
        self, actor: Actor, project_id: ProjectId, key: PageStepKey, name: str, value: object
    ) -> PageStepState:
        """Change one field of the parameters of a step for one page, and mark the stage of the page stale.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param key: The page, the stage and the step.
        :type key: PageStepKey
        :param name: Name of the field in the parameters of the step.
        :type name: str
        :param value: The value the page uses for the field, as JSON.
        :type value: object
        :returns: The state of the step on the page, with the field changed.
        :rtype: PageStepState
        :raises NotFoundError: If the actor has no such project, the project has no such page, no recipe of the stage
                               has the step, or its processor is not installed.
        :raises InvalidParametersError: If the processor has no such field, or the value is out of its range.
        """
        await self._check_page(actor, project_id, key.page_id)
        step = await find_step(self._uow.recipes, project_id, key)
        state = await self._state(key)
        checked = self._catalogue.get(step.processor_key).validate_params({**state.apply_to(step.params), name: value})
        return await self._store(project_id, state, {**state.params, name: checked[name]})

    async def reset(self, actor: Actor, project_id: ProjectId, key: PageStepKey, name: str) -> PageStepState:
        """Take a field back from the page, so the page runs with the value of the step of the recipe again.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param key: The page, the stage and the step.
        :type key: PageStepKey
        :param name: Name of the field in the parameters of the step.
        :type name: str
        :returns: The state of the step on the page after the change, which is empty, and no longer stored, when nothing
                  is left of it.
        :rtype: PageStepState
        :raises NotFoundError: If the actor has no such project, the project has no such page, or the page does not
                               change the field.
        """
        await self._check_page(actor, project_id, key.page_id)
        state = await self._state(key)
        if name not in state.params:
            raise NotFoundError(name)
        remaining = {field: value for field, value in state.params.items() if field != name}
        return await self._store(project_id, state, remaining)

    async def _check_page(self, actor: Actor, project_id: ProjectId, page_id: PageId) -> None:
        """Check that the project is the actor's and the page is one of its book.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :raises NotFoundError: If the actor has no such project, or the project has no such page.
        """
        await owned_project(self._uow.projects, actor, project_id)
        if (await self._uow.pages.get(page_id)).project_id != project_id:
            raise NotFoundError(page_id)

    async def _state(self, key: PageStepKey) -> PageStepState:
        """Return the state of a step on a page, or an empty one for a page that changes nothing of it yet.

        :param key: The page, the stage and the step.
        :type key: PageStepKey
        :returns: The state.
        :rtype: PageStepState
        """
        stored = await self._uow.page_step_states.find(key)
        return stored or PageStepState(
            page_id=key.page_id, stage=key.stage, step_id=key.step_id, updated_at=self._clock.now()
        )

    async def _store(self, project_id: ProjectId, state: PageStepState, params: MetadataMap) -> PageStepState:
        """Write the new settings of a state, the change that made them and the stale mark, and commit.

        A state with neither a setting nor an edit left is deleted. Settings equal to the stored ones change nothing
        and write nothing.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param state: The state as it is stored, or an empty one.
        :type state: PageStepState
        :param params: The fields the page changes from now on.
        :type params: MetadataMap
        :returns: The state after the change, which is empty when it was deleted.
        :rtype: PageStepState
        """
        if params == state.params:
            return state
        moment = self._clock.now()
        changed = evolve(state, params=params, updated_at=moment)
        if changed.is_empty:
            await self._uow.page_step_states.delete(state.key)
        else:
            await self._uow.page_step_states.save(changed)
        await self._uow.page_step_changes.add(
            PageStepChange(
                id=PageStepChangeId(uuid4()),
                page_id=state.page_id,
                stage=state.stage,
                step_id=state.step_id,
                layer=StepLayer.SETTINGS,
                before=dict(state.params) or None,
                after=dict(params) or None,
                source=ChangeSource.USER,
                created_at=moment,
            )
        )
        stale = await self._records.mark_stale(state.page_id, state.stage)
        await self._uow.commit()
        await self._records.announce(project_id, stale)
        return changed
