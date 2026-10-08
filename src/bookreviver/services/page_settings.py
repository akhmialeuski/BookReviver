"""The values a setting of a step has for a part of the pages: pages, the odd pages, the even pages, or a group.

A step of a recipe holds the parameters every page runs with. A field of them may have a value of its own for the
open page, for the pages the user selected, for the odd pages, for the even pages, or for the pages of a group. A page
takes each field from the strongest of those that has a value, a page before a group, a group before a side, and a side
before the recipe, so a field changed in the recipe reaches every page that has no value of its own for it.

The values of pages are kept in the ``PageStepState`` of each page, and the values of the odd pages, the even pages and
a group once for the step, as ``StepValues``, so a page added later, or moved to another place of the book, takes them
without being touched. ``StepValueLayers`` decides the order, here and in a run alike.

A value is checked as a field of the parameters of the processor of the step, together with the other fields the step
runs with on each page it reaches, so a field the processor does not have, or a value out of its range, is refused
before anything is stored. The value that is stored is the one the processor returns, which has its type made right.

Every change is written to the history of each page whose parameters it changes, in the ``settings`` layer, with the
whole layer before and after, and marks the stage of those pages stale, since the version they show was made with other
parameters. A page that already takes the value from a stronger part is untouched. Nothing is computed: a run of the
stage picks the values up like the parameters of the recipe.
"""

from typing import TYPE_CHECKING, Any, Self

from attrs import evolve, frozen

from bookreviver.domain.entities import PageStepState
from bookreviver.domain.enums import ChangeSource, PageOrigin, StepLayer, ValueScope
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.history import ValueChanges
from bookreviver.domain.step_values import (
    NOT_A_PART,
    PageStepSettings,
    StepValueLayers,
    StepValues,
    StepValuesKey,
)
from bookreviver.services.page_batches import PageBatch
from bookreviver.services.projects import book_pages, owned_page, owned_project

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from bookreviver.domain.entities import Actor, Page, PageStage
    from bookreviver.domain.enums import RecipeKind, Stage
    from bookreviver.domain.ids import PageId, ProjectId
    from bookreviver.domain.step_values import ValueField
    from bookreviver.domain.values import MetadataMap, Step
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.processing import ProcessorCatalog
    from bookreviver.ports.runtime import Clock
    from bookreviver.services.stage_records import StageRecords


@frozen(kw_only=True)
class _Scene:
    """The step, the pages a change reaches and the values those pages have, as one change reads them.

    :ivar field: The field of the step the change is for, and the pages it names.
    :ivar moment: When the change is made, which stamps the empty values and states a change writes.
    :ivar steps: The step as each recipe of the stage that has it holds it, by the kind of page the recipe processes.
    :ivar pages: The pages the change reaches, which are processed by the step, in book order.
    :ivar holder: The values of the part of the pages the target names, which are empty for a part that has none, and
                  None for a target of pages.
    :ivar positions: The place in the book, counted from 1, of each page that has one counted. Nothing reads them while
                     no part of the pages has a value for the step and the target is pages, so none are counted then.
    :ivar parts: The values of the odd pages, the even pages and the groups for the step, as they are stored.
    :ivar states: The state of the step on each of the pages that has one.
    """

    field: ValueField
    moment: datetime
    steps: Mapping[RecipeKind, Step]
    pages: Sequence[Page]
    holder: StepValues | None
    positions: Mapping[PageId, int]
    parts: Sequence[StepValues]
    states: Mapping[PageId, PageStepState]

    @classmethod
    async def load(
        cls, uow: UnitOfWork, actor: Actor, project_id: ProjectId, field: ValueField, moment: datetime
    ) -> Self:
        """Read the step, the pages the field names and the values they have.

        :param uow: Unit of work the reads go through.
        :type uow: UnitOfWork
        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param field: The field of the step the change is for, and the pages it names.
        :type field: ValueField
        :param moment: When the change is made.
        :type moment: datetime
        :returns: The scene.
        :rtype: Self
        :raises NotFoundError: If the actor has no such project, no recipe of the stage has the step, a page is not one
                               of the project, or no page the target reaches is processed by the step.
        """
        await owned_project(uow.projects, actor, project_id)
        step_id, target = field.step_id, field.target
        steps = {
            recipe.kind: step
            for recipe in await uow.recipes.list_for_stage(project_id, field.stage)
            for step in recipe.steps
            if step.step_id == step_id
        }
        if not steps:
            raise NotFoundError(step_id)
        parts = await uow.step_values.list_for_step(project_id, step_id)
        if target.scope is ValueScope.PAGES:
            holder = None
            named = await uow.pages.list_by_ids(project_id, target.page_ids)
            positions = {page.id: await uow.pages.count_before(page) + 1 for page in named} if parts else {}
        else:
            holder = await uow.step_values.find(
                StepValuesKey(project_id, step_id, target.scope, target.group_label)
            ) or StepValues(
                project_id=project_id,
                stage=field.stage,
                step_id=step_id,
                scope=target.scope,
                group_label=target.group_label,
                updated_at=moment,
            )
            named = await book_pages(uow.pages, project_id)
            positions = {page.id: index for index, page in enumerate(named, start=1)}
        pages = [
            page
            for page in named
            if page.recipe_kind in steps
            and not page.is_leaf
            and page.origin is not PageOrigin.PLACEHOLDER
            and (holder is None or holder.covers(group_label=page.group_label, position=positions[page.id]))
        ]
        if not pages:
            raise NotFoundError(step_id)
        states = await uow.page_step_states.list_for_step([page.id for page in pages], field.stage, step_id)
        return cls(
            field=field,
            moment=moment,
            steps=steps,
            pages=pages,
            holder=holder,
            positions=positions,
            parts=parts,
            states={state.page_id: state for state in states},
        )

    def own(self, page: Page) -> MetadataMap:
        """Return the fields a page changes for itself.

        :param page: The page.
        :type page: Page
        :returns: The fields, which are none for a page that has no state of the step.
        :rtype: MetadataMap
        """
        stored = self.states.get(page.id)
        return {} if stored is None else stored.params

    def state_of(self, page: Page) -> PageStepState:
        """Return the state of the step on a page, or an empty one for a page that has none.

        :param page: The page.
        :type page: Page
        :returns: The state.
        :rtype: PageStepState
        """
        return self.states.get(page.id) or PageStepState(
            page_id=page.id, stage=self.field.stage, step_id=self.field.step_id, updated_at=self.moment
        )

    def replacing(self, params: MetadataMap) -> list[StepValues]:
        """Give the values of the parts of the pages with the part the target names set to new fields.

        :param params: The fields the part changes from now on, which are none to take the part away.
        :type params: MetadataMap
        :returns: The stored values of the other parts, and the new values of this one unless they are empty.
        :rtype: list[StepValues]
        :raises ValueError: If the target is not a part of the pages.
        """
        if self.holder is None:
            raise ValueError(NOT_A_PART)
        others = [part for part in self.parts if part.key != self.holder.key]
        return [*others, evolve(self.holder, params=params)] if params else others

    def effective(
        self, page: Page, *, parts: Sequence[StepValues] | None = None, own: MetadataMap | None = None
    ) -> dict[str, Any]:
        """Work out what the step runs with on a page, as it is stored or with some of it replaced.

        :param page: The page.
        :type page: Page
        :param parts: The values of the parts of the pages to use instead of the stored ones, or None to keep them.
        :type parts: Sequence[StepValues] | None
        :param own: The fields the page changes for itself to use instead of the stored ones, or None to keep them.
        :type own: MetadataMap | None
        :returns: The parameters of the step on the page.
        :rtype: dict[str, Any]
        """
        return StepValueLayers(self.parts if parts is None else parts).lay_over(
            self.steps[page.recipe_kind].params,
            group_label=page.group_label,
            position=self.positions.get(page.id, 0),
            own=self.own(page) if own is None else own,
        )


class PageSettingsService:
    """Lists, sets and takes back the values of the steps for parts of the pages of the acting account's projects."""

    def __init__(self, *, uow: UnitOfWork, catalogue: ProcessorCatalog, records: StageRecords, clock: Clock) -> None:
        """Work over the ports of one request.

        :param uow: Unit of work of the request, whose commit ends every changing use case.
        :type uow: UnitOfWork
        :param catalogue: The processors the application can run, which check the value of a field.
        :type catalogue: ProcessorCatalog
        :param records: Writer of the stage records, which a changed value marks stale.
        :type records: StageRecords
        :param clock: Clock stamping the states, the values and the changes.
        :type clock: Clock
        """
        self._uow = uow
        self._catalogue = catalogue
        self._records = records
        self._clock = clock

    async def list(
        self, actor: Actor, project_id: ProjectId, page_id: PageId, stage: Stage
    ) -> Sequence[PageStepSettings]:
        """List the steps of one stage of a page that have values of the page or of a part of the pages.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :param stage: The stage whose steps are listed.
        :type stage: Stage
        :returns: The steps of the recipe of the page that have a value of the page or a value of any part of the pages,
                  in the order of the recipe, each with the parameters it runs with on the page.
        :rtype: Sequence[PageStepSettings]
        :raises NotFoundError: If the actor has no such project, or the project has no such page.
        """
        page = await owned_page(self._uow, actor, project_id, page_id)
        recipes = await self._uow.recipes.list_for_stage(project_id, stage)
        recipe = next((recipe for recipe in recipes if recipe.kind is page.recipe_kind), None)
        listed: list[PageStepSettings] = []
        if recipe is None:
            return listed
        parts = await self._uow.step_values.list_for_project(project_id)
        states = {state.step_id: state for state in await self._uow.page_step_states.list_for_page(page_id, stage)}
        position = await self._uow.pages.count_before(page) + 1
        for step in recipe.steps:
            of_step = [part for part in parts if part.step_id == step.step_id]
            state = states.get(step.step_id)
            own = {} if state is None else state.params
            if not of_step and not own:
                continue
            effective = StepValueLayers(of_step).lay_over(
                step.params, group_label=page.group_label, position=position, own=own
            )
            listed.append(
                PageStepSettings(
                    step_id=step.step_id,
                    params=own,
                    parts=of_step,
                    effective=effective,
                    updated_at=state.updated_at if own and state is not None else None,
                )
            )
        return listed

    async def change(self, actor: Actor, project_id: ProjectId, field: ValueField, value: object) -> ValueChanges:
        """Set one field of the parameters of a step for the pages the target names, and mark their stages stale.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param field: The field and the pages, the odd pages, the even pages or the group the value is for.
        :type field: ValueField
        :param value: The value they use for the field, as JSON.
        :type value: object
        :returns: The batch and the changes written, one on each page whose parameters change, which are none when the
                  value is the one they have.
        :rtype: ValueChanges
        :raises NotFoundError: If the actor has no such project, a page is not one of the project, no recipe of the
                               stage has the step, or no page the target names is processed by it.
        :raises InvalidParametersError: If the processor has no such field, or the value is out of its range on a page
                                        it reaches.
        """
        scene = await _Scene.load(self._uow, actor, project_id, field, self._clock.now())
        name = field.name
        processor = self._catalogue.get(next(iter(scene.steps.values())).processor_key)
        if scene.holder is None:
            own = {
                page.id: {
                    **scene.own(page),
                    name: processor.validate_params(scene.effective(page, own={**scene.own(page), name: value}))[name],
                }
                for page in scene.pages
            }
            return await self._store(project_id, scene, own=own, params=None)
        # The field is checked on its own too, since a stronger part may give every page another value of it
        checked = [
            processor.validate_params({**step.params, **scene.holder.params, name: value})[name]
            for step in scene.steps.values()
        ]
        params = {**scene.holder.params, name: checked[0]}
        for page in scene.pages:
            processor.validate_params(scene.effective(page, parts=scene.replacing(params)))
        return await self._store(project_id, scene, own={}, params=params)

    async def reset(self, actor: Actor, project_id: ProjectId, field: ValueField) -> ValueChanges:
        """Take a field back from the pages the target names, so they take it from the next part, and mark them stale.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param field: The field and the pages, the odd pages, the even pages or the group the value was set for.
        :type field: ValueField
        :returns: The batch and the changes written, one on each page whose parameters change.
        :rtype: ValueChanges
        :raises NotFoundError: If the actor has no such project, a page is not one of the project, no recipe of the
                               stage has the step, or none of the pages the target names has a value for the field.
        """
        scene = await _Scene.load(self._uow, actor, project_id, field, self._clock.now())
        name = field.name
        if scene.holder is None:
            own = {
                page.id: {other: value for other, value in scene.own(page).items() if other != name}
                for page in scene.pages
                if name in scene.own(page)
            }
            if not own:
                raise NotFoundError(name)
            return await self._store(project_id, scene, own=own, params=None)
        if name not in scene.holder.params:
            raise NotFoundError(name)
        params = {other: value for other, value in scene.holder.params.items() if other != name}
        return await self._store(project_id, scene, own={}, params=params)

    async def _store(
        self, project_id: ProjectId, scene: _Scene, *, own: Mapping[PageId, MetadataMap], params: MetadataMap | None
    ) -> ValueChanges:
        """Write the new values, the changes that made them and the stale marks, and commit.

        A state with neither a setting nor an edit left is deleted, and values with no field left are deleted. Values
        equal to the stored ones change nothing and write nothing. The stage of a page is marked stale only when what
        the step runs with on it changes, so a page that takes the field from a stronger part is left alone.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param scene: What the change reads.
        :type scene: _Scene
        :param own: The fields each page changes from now on, for a target of pages.
        :type own: Mapping[PageId, MetadataMap]
        :param params: The fields the part of the pages the target names changes from now on, for a target of a part.
        :type params: MetadataMap | None
        :returns: The batch and the changes written.
        :rtype: ValueChanges
        """
        batch = PageBatch(uow=self._uow, source=ChangeSource.USER, moment=scene.moment)
        reached: list[PageId] = []
        if scene.holder is None:
            for page in scene.pages:
                state = scene.state_of(page)
                if page.id not in own or own[page.id] == state.params:
                    continue
                if scene.effective(page, own=own[page.id]) != scene.effective(page):
                    reached.append(page.id)
                await batch.write(state, StepLayer.SETTINGS, own[page.id] or None)
        else:
            after = scene.replacing(params or {})
            reached = [page.id for page in scene.pages if scene.effective(page, parts=after) != scene.effective(page)]
            await batch.write_values(scene.holder, params or {}, reached)
        changes = await batch.flush()
        stale: list[PageStage] = []
        for page_id in reached:
            stale.extend(await self._records.mark_stale(page_id, scene.field.stage))
        await self._uow.commit()
        await self._records.announce(project_id, stale)
        return ValueChanges(batch_id=batch.batch_id, changes=tuple(changes))
