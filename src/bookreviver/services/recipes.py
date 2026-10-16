"""The recipes of a project: the ones every stage starts with, and the check of the steps a user puts into one.

A stage that has recipes has exactly one for each kind of page (``RecipeKind``), and a page is processed by the recipe
of its kind. The first time a stage is asked for, ``RecipeBook`` creates all of them from ``DefaultRecipes``. A kind
whose template the catalogue cannot build, such as one that needs OpenCV on a machine that has none, takes the steps of
the recipe of text pages, and a stage with no template of text pages and no default profile has no recipe at all. When
the owner of the project has chosen a default profile for the stage, the recipe of text pages is made from the profile.

Every step is checked before it is saved: its processor must exist, belong to the stage of the recipe, and accept its
parameters, which are stored in the form ``validate_params`` returns, defaults filled in, so two recipes that differ in
spelling alone are equal and give the same page versions. The order of the steps is checked too, by ``RecipeOrder``: a
step that stands where its processor cannot work is refused unless the draft asks for the free order, and one that
stands off its usual place is saved, and the routes report it through ``RecipeOrder.issues``.

The work of a transaction is synchronous and cannot await, so what a work needs from the book of recipes is synchronous
and takes the ``Repositories`` of its transaction: ``recipes_in``, ``get``, ``rewrite``, ``default_draft`` and
``find_step``, with ``check`` and ``installed``, which read no repository. The asynchronous ``recipes``, ``of_kind`` and
``for_pages`` run on the unit of work themselves, ``recipes`` opening a ``change_book`` of its own to store the
defaults, so they are called outside any work, and inside a work the synchronous ``recipes_in`` is used.
"""

from datetime import timedelta
from typing import TYPE_CHECKING, ClassVar
from uuid import uuid4

from attrs import evolve, field, frozen

from bookreviver.domain.entities import Recipe
from bookreviver.domain.enums import (
    BinarizationMethod,
    DeskewMethod,
    DewarpMethod,
    OrderMode,
    OutputMode,
    RecipeKind,
    Stage,
)
from bookreviver.domain.errors import InvalidParametersError, NotFoundError
from bookreviver.domain.ids import RecipeId
from bookreviver.domain.values import RecipeDraft, Step

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from bookreviver.domain.entities import Page, Project, RecipeProfile
    from bookreviver.domain.ids import PageId, ProjectId
    from bookreviver.domain.values import MetadataMap, PageStepKey
    from bookreviver.ports.persistence import RecipeRepository, Repositories, UnitOfWork
    from bookreviver.ports.processing import ProcessorCatalog
    from bookreviver.ports.runtime import Clock
    from bookreviver.services.recipe_order import RecipeOrder

NO_STEPS: str = 'A recipe needs at least one step.'
ALL_STEPS_OFF: str = 'A recipe needs at least one step that is switched on.'
WRONG_STAGE: str = 'The processor {key} belongs to the {actual} stage, not to the {expected} stage.'
UNKNOWN_PROCESSOR: str = 'There is no processor {key}.'
NO_RECIPE: str = 'The {stage} stage has no recipe, since no processor for it is installed.'
# The steps of every geometry recipe a project starts with, which the recipes switch and tune differently
GEOMETRY_STEPS: tuple[str, ...] = (
    'geometry.perspective',
    'geometry.deskew',
    'geometry.dewarp',
    'geometry.crop',
    'geometry.normalize',
)


def find_step(recipes: RecipeRepository, project_id: ProjectId, key: PageStepKey) -> Step:
    """Find a step by its identifier in the recipes of its stage, where a copy of a step keeps the identifier.

    It is called from the work of a transaction.

    :param recipes: Repository of the transaction to read the recipes from.
    :type recipes: RecipeRepository
    :param project_id: Project owning the recipes.
    :type project_id: ProjectId
    :param key: The page, the stage and the step.
    :type key: PageStepKey
    :returns: The step as the first recipe of the stage that has it holds it.
    :rtype: Step
    :raises NotFoundError: If no recipe of the stage has the step.
    """
    for recipe in recipes.list_for_stage(project_id, key.stage):
        for step in recipe.steps:
            if step.step_id == key.step_id:
                return step
    raise NotFoundError(key.step_id)


@frozen(kw_only=True)
class RecipeTemplate:
    """The steps of a recipe a stage starts with for one kind of page.

    :ivar processor_keys: Keys of the processors of the steps, which run with their default parameters unless the
                          template gives others.
    :ivar params: Parameters of some of the steps, by the key of the processor, which the defaults fill out.
    :ivar off: Keys of the processors whose steps are in the recipe but switched off.
    """

    processor_keys: tuple[str, ...]
    params: Mapping[str, MetadataMap] = field(factory=dict)
    off: frozenset[str] = frozenset()


# The steps of the Cleanup stage in the order they run: the page is made black and white first, so that what is left
# of the dust is separate small spots, which are then removed, the strokes are made thinner or thicker, and the user's
# eraser comes last
BINARIZE_KEY: str = 'cleanup.binarize'
DESPECKLE_KEY: str = 'cleanup.despeckle'
THICKNESS_KEY: str = 'cleanup.thickness'
ERASER_KEY: str = 'cleanup.eraser'
# The parameters the templates of the stage set
MODE_PARAM: str = 'mode'
METHOD_PARAM: str = 'method'
STRENGTH_PARAM: str = 'strength'

# The recipes of a page of each kind. A page of text is followed along its lines, and a blank page is processed as one
# of text, since a scan of a blank page has the paper and the specks of a page of text. A picture is not followed along
# lines of text and keeps its tones.
AUTOMATIC_SPLIT = RecipeTemplate(processor_keys=('split.auto',))
TEXT_GEOMETRY = RecipeTemplate(
    processor_keys=GEOMETRY_STEPS,
    params={
        'geometry.deskew': {'method': DeskewMethod.PROJECTION},
        'geometry.dewarp': {'method': DewarpMethod.TEXT_LINES},
    },
)
PICTURE_GEOMETRY = RecipeTemplate(
    processor_keys=GEOMETRY_STEPS,
    params={
        'geometry.deskew': {'method': DeskewMethod.HOUGH},
        'geometry.dewarp': {'method': DewarpMethod.PAGE_EDGES},
    },
)
TEXT_CLEANUP = RecipeTemplate(
    processor_keys=(BINARIZE_KEY, DESPECKLE_KEY, THICKNESS_KEY, ERASER_KEY),
    params={
        BINARIZE_KEY: {MODE_PARAM: OutputMode.BW, METHOD_PARAM: BinarizationMethod.SAUVOLA},
        DESPECKLE_KEY: {STRENGTH_PARAM: 2},
    },
)
PICTURE_CLEANUP = RecipeTemplate(
    processor_keys=(BINARIZE_KEY, ERASER_KEY),
    params={BINARIZE_KEY: {MODE_PARAM: OutputMode.GRAY, METHOD_PARAM: BinarizationMethod.SAUVOLA}},
)


class DefaultRecipes:
    """The templates of the recipes each stage starts with, one for each kind of page."""

    TEMPLATES: ClassVar[Mapping[Stage, Mapping[RecipeKind, RecipeTemplate]]] = {
        Stage.PAGE_SPLIT: dict.fromkeys(RecipeKind, AUTOMATIC_SPLIT),
        Stage.GEOMETRY: {
            RecipeKind.TEXT: TEXT_GEOMETRY,
            RecipeKind.COLOR_PICTURE: PICTURE_GEOMETRY,
            RecipeKind.BW_PICTURE: PICTURE_GEOMETRY,
            RecipeKind.BLANK: TEXT_GEOMETRY,
        },
        Stage.CLEANUP: {
            RecipeKind.TEXT: TEXT_CLEANUP,
            RecipeKind.COLOR_PICTURE: PICTURE_CLEANUP,
            RecipeKind.BW_PICTURE: PICTURE_CLEANUP,
            RecipeKind.BLANK: TEXT_CLEANUP,
        },
    }

    def __init__(self, templates: Mapping[Stage, Mapping[RecipeKind, RecipeTemplate]] | None = None) -> None:
        """Start from the given templates, or from the ones every project gets.

        :param templates: Templates by stage and kind, or None for ``TEMPLATES``.
        :type templates: Mapping[Stage, Mapping[RecipeKind, RecipeTemplate]] | None
        """
        self._templates = self.TEMPLATES if templates is None else templates

    def for_stage(self, stage: Stage) -> Mapping[RecipeKind, RecipeTemplate]:
        """Return the templates of a stage by the kind of page they are for.

        :param stage: The stage.
        :type stage: Stage
        :returns: The templates, none for a stage that has no recipe by default.
        :rtype: Mapping[RecipeKind, RecipeTemplate]
        """
        return self._templates.get(stage, dict[RecipeKind, RecipeTemplate]())


class RecipeBook:
    """Finds the recipes of a project, creates the default ones, and checks the steps of a recipe."""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        catalogue: ProcessorCatalog,
        defaults: DefaultRecipes,
        clock: Clock,
        order: RecipeOrder,
    ) -> None:
        """Work over the ports of one request or job.

        :param uow: Unit of work, on which ``recipes`` reads and opens a ``change_book`` of its own to store the
                    defaults. The synchronous methods use the repositories their caller passes and never this.
        :type uow: UnitOfWork
        :param catalogue: The processors the application can run.
        :type catalogue: ProcessorCatalog
        :param defaults: The recipes a stage starts with.
        :type defaults: DefaultRecipes
        :param clock: Clock stamping new recipes.
        :type clock: Clock
        :param order: The check of the order of the steps.
        :type order: RecipeOrder
        """
        self._uow = uow
        self._catalogue = catalogue
        self._defaults = defaults
        self._clock = clock
        self._order = order

    async def recipes(
        self, project_id: ProjectId, stage: Stage, *, checking: Callable[[Repositories], object] | None = None
    ) -> list[Recipe]:
        """Return the recipes of a stage, one for each kind of page, creating them when the stage is first asked for.

        Called outside any work, since it runs on the unit of work itself. It reads in a transaction that only reads,
        and opens a ``change_book`` of its own only when the stage has no recipes yet, where ``recipes_in`` reads again,
        since a task that ran at the same time may have stored them first, and adds the defaults only if they are still
        missing. The work of a transaction cannot call this, and calls ``recipes_in`` instead.

        A caller that has to read something else before it goes on, such as the project of the actor, hands the read in
        as ``checking``, which runs first in the transaction that reads the recipes. When the recipes are stored that
        transaction is the only one, where a read of the caller beside this call would be a second snapshot of the
        database.

        :param project_id: Project owning the recipes.
        :type project_id: ProjectId
        :param stage: The stage.
        :type stage: Stage
        :param checking: Function over the repositories of the reading transaction, run before the recipes are read,
                         whose exceptions go to the caller, or None for no check.
        :type checking: Callable[[Repositories], object] | None
        :returns: The recipes in the order of the kinds.
        :rtype: list[Recipe]
        :raises NotFoundError: If the project does not exist, the stage has no recipe by default, or none of its
                               processors is installed.
        """

        def read(repos: Repositories) -> list[Recipe]:
            """Run the check of the caller, then read the stored recipes of the stage.

            :param repos: Repositories of the transaction that only reads.
            :type repos: Repositories
            :returns: The stored recipes, none before the stage has been used.
            :rtype: list[Recipe]
            """
            if checking is not None:
                checking(repos)
            return self._stored(repos, project_id, stage)

        if found := await self._uow.read(read):
            return found
        return await self._uow.change_book(project_id, lambda repos, project: self.recipes_in(repos, project, stage))

    def recipes_in(self, repos: Repositories, project: Project, stage: Stage) -> list[Recipe]:
        """Return the recipes of a stage, creating them when the stage is first asked for, in the transaction of a work.

        This is the form to use inside the work of ``change_book`` or ``change``, where ``recipes`` cannot be awaited.
        It reads the recipes and, when the stage has none, stores the defaults through the same repositories, so the
        defaults are committed with the rest of the work.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: Repositories
        :param project: Project owning the recipes, whose owner's default profile the first recipes may be made from.
        :type project: Project
        :param stage: The stage.
        :type stage: Stage
        :returns: The recipes in the order of the kinds.
        :rtype: list[Recipe]
        :raises NotFoundError: If the stage has no recipe by default, or none of its processors is installed.
        """
        if found := self._stored(repos, project.id, stage):
            return found
        moment = self._clock.now()
        steps, profile = self._starting_steps(repos, project, stage)
        built = [
            Recipe(
                id=RecipeId(uuid4()),
                project_id=project.id,
                stage=stage,
                kind=kind,
                steps=steps[kind],
                profile_id=profile.id if profile is not None and kind is RecipeKind.TEXT else None,
                # A microsecond apart, so the recipes are listed in the order of the kinds
                created_at=moment + timedelta(microseconds=index),
                updated_at=moment,
            )
            for index, kind in enumerate(RecipeKind)
        ]
        repos.recipes.add_many(built)
        return built

    def _stored(self, repos: Repositories, project_id: ProjectId, stage: Stage) -> list[Recipe]:
        """Read the stored recipes of a stage, each with the parameters of its steps as their processors write them now.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: Repositories
        :param project_id: Project owning the recipes.
        :type project_id: ProjectId
        :param stage: The stage.
        :type stage: Stage
        :returns: The recipes in the order they were created, which are none before the stage has been used.
        :rtype: list[Recipe]
        """
        return [self._current(recipe) for recipe in repos.recipes.list_for_stage(project_id, stage)]

    def _current(self, recipe: Recipe) -> Recipe:
        """Give a stored recipe with the parameters of every step as their processors write them now.

        A processor that gains a parameter after a recipe was saved leaves the stored step without it, while a form
        opened on the step fills it in and so changes the draft, which no one made. The stored recipe is left as it is,
        and a step whose processor is gone or refuses its stored parameters is given as stored, which a run reports.

        :param recipe: The recipe as stored.
        :type recipe: Recipe
        :returns: The recipe with the checked parameters of each step it could check.
        :rtype: Recipe
        """
        steps: list[Step] = []
        for step in recipe.steps:
            try:
                steps.append(self._check_step(recipe.stage, step))
            except InvalidParametersError:
                steps.append(step)
        return evolve(recipe, steps=tuple(steps))

    async def of_kind(self, project_id: ProjectId, stage: Stage, kind: RecipeKind) -> Recipe:
        """Return the recipe of a stage for one kind of page, called outside any work as ``recipes`` is.

        :param project_id: Project owning the recipe.
        :type project_id: ProjectId
        :param stage: The stage.
        :type stage: Stage
        :param kind: The kind of page.
        :type kind: RecipeKind
        :returns: The recipe.
        :rtype: Recipe
        :raises NotFoundError: If the stage has no recipe by default, or none of its processors is installed.
        """
        return next(recipe for recipe in await self.recipes(project_id, stage) if recipe.kind is kind)

    async def for_pages(self, project_id: ProjectId, stage: Stage, pages: Sequence[Page]) -> dict[PageId, Recipe]:
        """Choose the recipe of each page of a stage, which is the recipe of the kind of the page.

        Called outside any work, as ``recipes`` is.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param stage: The stage.
        :type stage: Stage
        :param pages: The pages.
        :type pages: Sequence[Page]
        :returns: The recipe of each page, by page identifier.
        :rtype: dict[PageId, Recipe]
        :raises NotFoundError: If the stage has no recipe by default, or none of its processors is installed.
        """
        by_kind = {recipe.kind: recipe for recipe in await self.recipes(project_id, stage)}
        return {page.id: by_kind[page.recipe_kind] for page in pages}

    def default_draft(self, repos: Repositories, project: Project, stage: Stage, kind: RecipeKind) -> RecipeDraft:
        """Return the draft of the steps the recipe of a kind starts with, which a reset puts back into the recipe.

        The recipe of text pages takes the default profile of the book's owner first, when there is a usable one, and
        the draft names it, so the recipe can be linked to it. Without one, and for any other kind, the steps are those
        of the built-in template of the kind, or of the recipe of text pages when the template cannot be built here.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: Repositories
        :param project: Project the steps are for.
        :type project: Project
        :param stage: The stage.
        :type stage: Stage
        :param kind: The kind of page the recipe is for.
        :type kind: RecipeKind
        :returns: The draft, whose steps are checked and each have a new identifier, in the free order.
        :rtype: RecipeDraft
        :raises NotFoundError: If the stage has no recipe by default, or none of its processors is installed.
        """
        steps, profile = self._starting_steps(repos, project, stage)
        profile_id = profile.id if profile is not None and kind is RecipeKind.TEXT else None
        return RecipeDraft(steps=steps[kind], order=OrderMode.FREE, profile_id=profile_id)

    def get(
        self, repos: Repositories, project_id: ProjectId, recipe_id: RecipeId, *, stage: Stage | None = None
    ) -> Recipe:
        """Return a recipe of the project, of the given stage when one is named.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: Repositories
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param recipe_id: Identifier of the recipe.
        :type recipe_id: RecipeId
        :param stage: Stage the recipe must process, or None for any.
        :type stage: Stage | None
        :returns: The recipe.
        :rtype: Recipe
        :raises NotFoundError: If the recipe does not exist, belongs to another project, or processes another stage.
        """
        recipe = repos.recipes.get(recipe_id)
        if recipe.project_id != project_id or (stage is not None and recipe.stage is not stage):
            raise NotFoundError(recipe_id)
        return recipe

    def rewrite(self, repos: Repositories, recipe: Recipe, draft: RecipeDraft) -> Recipe:
        """Store new steps of a recipe, in the transaction of the caller, which this never opens.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: Repositories
        :param recipe: The recipe to change.
        :type recipe: Recipe
        :param draft: New steps, which are checked against their processors and their order.
        :type draft: RecipeDraft
        :returns: The recipe as stored, which the transaction of the caller commits.
        :rtype: Recipe
        :raises InvalidParametersError: If a step does not fit its processor, or stands off a required place in the
                                        usual order.
        """
        changed = evolve(
            recipe,
            steps=self.check(recipe.stage, draft.steps, order=draft.order),
            updated_at=self._clock.now(),
        )
        repos.recipes.update(changed)
        return changed

    def check(self, stage: Stage, steps: Sequence[Step], *, order: OrderMode = OrderMode.USUAL) -> tuple[Step, ...]:
        """Check the steps of a recipe of a stage and return them with their parameters in checked form.

        :param stage: Stage the recipe processes.
        :type stage: Stage
        :param steps: The steps as a user gave them.
        :type steps: Sequence[Step]
        :param order: Whether a step that stands where it cannot work is refused or only warned of. Steps that were
                      stored before, or that are only previewed, are checked in the free order.
        :type order: OrderMode
        :returns: The steps with the defaults of each processor filled in.
        :rtype: tuple[Step, ...]
        :raises InvalidParametersError: If there is no step or every step is switched off, a processor does not exist
                                        or belongs to another stage, a parameter is wrong, or a step stands off a
                                        required place in the usual order.
        """
        if not steps:
            raise InvalidParametersError(NO_STEPS)
        if not any(step.enabled for step in steps):
            raise InvalidParametersError(ALL_STEPS_OFF)
        checked = tuple(self._check_step(stage, step) for step in steps)
        self._order.enforce(checked, order)
        return checked

    def _check_step(self, stage: Stage, step: Step) -> Step:
        """Check one step.

        :param stage: Stage the recipe processes.
        :type stage: Stage
        :param step: The step as a user gave it.
        :type step: Step
        :returns: The step with its checked parameters.
        :rtype: Step
        :raises InvalidParametersError: If the processor does not exist or belongs to another stage, or a parameter is
                                        wrong.
        """
        try:
            processor = self._catalogue.get(step.processor_key)
        except NotFoundError as error:
            raise InvalidParametersError(UNKNOWN_PROCESSOR.format(key=step.processor_key)) from error
        if processor.spec.stage is not stage:
            err_msg = WRONG_STAGE.format(
                key=step.processor_key, actual=processor.spec.stage.label, expected=stage.label
            )
            raise InvalidParametersError(err_msg)
        return evolve(step, params=processor.validate_params(step.params))

    def installed(self, steps: Sequence[Step]) -> tuple[tuple[Step, ...], tuple[str, ...]]:
        """Split steps into those whose processor the catalogue offers and the keys of the processors it does not.

        :param steps: Steps of a saved recipe, whose processors may have been removed from the application since.
        :type steps: Sequence[Step]
        :returns: The steps that can run, in their order, and the distinct keys of the processors that are missing.
        :rtype: tuple[tuple[Step, ...], tuple[str, ...]]
        """
        kept = tuple(step for step in steps if self._offers(step.processor_key))
        missing = dict.fromkeys(step.processor_key for step in steps if not self._offers(step.processor_key))
        return kept, tuple(missing)

    def _buildable(self, stage: Stage) -> dict[RecipeKind, RecipeTemplate]:
        """List the built-in templates of a stage whose processors are all installed.

        :param stage: The stage.
        :type stage: Stage
        :returns: The templates the application can build, by the kind of page.
        :rtype: dict[RecipeKind, RecipeTemplate]
        """
        return {
            kind: template
            for kind, template in self._defaults.for_stage(stage).items()
            if all(self._offers(key) for key in template.processor_keys)
        }

    def _template_steps(self, template: RecipeTemplate) -> tuple[Step, ...]:
        """Build the steps of a built-in template, each with a new identifier.

        :param template: Template whose processors are installed.
        :type template: RecipeTemplate
        :returns: The steps with the parameters of the template and the defaults of each processor.
        :rtype: tuple[Step, ...]
        """
        return tuple(
            Step(
                processor_key=key,
                params=self._catalogue.get(key).validate_params(template.params.get(key, {})),
                enabled=key not in template.off,
            )
            for key in template.processor_keys
        )

    def _starting_steps(
        self, repos: Repositories, project: Project, stage: Stage
    ) -> tuple[dict[RecipeKind, tuple[Step, ...]], RecipeProfile | None]:
        """Give the steps the recipe of each kind of a stage starts with, each step with an identifier of its own.

        The recipe of text pages starts with the steps of the default profile of the book's owner when there is a usable
        one, and otherwise with the steps of its template. A kind whose template the catalogue cannot build starts with
        the steps of text pages, which a copy of a step keeps the identifier of.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: Repositories
        :param project: Project the steps are for.
        :type project: Project
        :param stage: The stage.
        :type stage: Stage
        :returns: The steps of each kind, and the profile the steps of text pages come from, or None.
        :rtype: tuple[dict[RecipeKind, tuple[Step, ...]], RecipeProfile | None]
        :raises NotFoundError: If the stage has no recipe by default, or none of its processors is installed.
        """
        templates = self._buildable(stage)
        preferred = self._default_profile(repos, project, stage)
        if preferred is None and RecipeKind.TEXT not in templates:
            raise NotFoundError(NO_RECIPE.format(stage=stage.label))
        profile, text_steps = (
            (None, self._template_steps(templates[RecipeKind.TEXT])) if preferred is None else preferred
        )
        others = [kind for kind in RecipeKind if kind is not RecipeKind.TEXT]
        steps = {RecipeKind.TEXT: text_steps}
        steps.update(
            (kind, self._template_steps(templates[kind]) if kind in templates else text_steps) for kind in others
        )
        return steps, profile

    def _default_profile(
        self, repos: Repositories, project: Project, stage: Stage
    ) -> tuple[RecipeProfile, tuple[Step, ...]] | None:
        """Take the default profile of the project's owner with its checked steps, if there is a usable one.

        A step whose processor is not installed is left out. A profile that has no step left to run, or whose steps no
        longer fit their processors, is passed over, so the stage starts with the built-in recipes as it does for an
        account without a default profile.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: Repositories
        :param project: Project the steps are for.
        :type project: Project
        :param stage: The stage.
        :type stage: Stage
        :returns: The profile and its steps as a recipe runs them, or None.
        :rtype: tuple[RecipeProfile, tuple[Step, ...]] | None
        """
        if (profile := repos.recipe_profiles.find_default(project.owner_id, stage)) is None:
            return None
        try:
            steps = self.check(stage, self.installed(profile.steps)[0], order=OrderMode.FREE)
        except InvalidParametersError:
            return None
        return profile, steps

    def _offers(self, key: str) -> bool:
        """Tell whether the catalogue has a processor.

        :param key: Key of the processor.
        :type key: str
        :returns: Whether the processor can be run here.
        :rtype: bool
        """
        try:
            self._catalogue.get(key)
        except NotFoundError:
            return False
        return True
