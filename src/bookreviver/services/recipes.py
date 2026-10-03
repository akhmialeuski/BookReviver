"""The recipes of a project: the ones every stage starts with, and the check of the steps a user puts into one.

A stage has exactly one active recipe, by which a page without a choice of its own is processed. The first time a
stage is asked for, ``RecipeBook`` creates its recipes from ``DefaultRecipes``: the first of the stage is active and
the others are variants the user may try. A template whose processor the catalogue does not offer, such as one that
needs OpenCV on a machine that has none, is left out, and a stage with no template left has no recipe at all. When the
owner of the project has chosen a default profile for the stage, the active recipe is made from the profile and the
templates follow it as variants.

Every step is checked before it is saved: its processor must exist, belong to the stage of the recipe, and accept its
parameters, which are stored in the form ``validate_params`` returns, defaults filled in, so two recipes that differ in
spelling alone are equal and give the same page versions.
"""

from datetime import timedelta
from typing import TYPE_CHECKING, ClassVar
from uuid import uuid4

from attrs import evolve, field, frozen

from bookreviver.domain.entities import Recipe, RecipeRule
from bookreviver.domain.enums import (
    BinarizationMethod,
    DeskewMethod,
    DewarpMethod,
    OutputMode,
    RuleCondition,
    Stage,
)
from bookreviver.domain.errors import ConflictError, InvalidParametersError, NotFoundError
from bookreviver.domain.ids import RecipeId, RecipeRuleId
from bookreviver.domain.values import Step

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from bookreviver.domain.ids import ProjectId
    from bookreviver.domain.values import MetadataMap, PageStepKey
    from bookreviver.ports.persistence import RecipeRepository, UnitOfWork
    from bookreviver.ports.processing import ProcessorCatalog
    from bookreviver.ports.runtime import Clock

NO_STEPS: str = 'A recipe needs at least one step.'
ALL_STEPS_OFF: str = 'A recipe needs at least one step that is switched on.'
WRONG_STAGE: str = 'The processor {key} belongs to the {actual} stage, not to the {expected} stage.'
UNKNOWN_PROCESSOR: str = 'There is no processor {key}.'
NO_RECIPE: str = 'The {stage} stage has no recipe, since no processor for it is installed.'
# The steps of every geometry recipe a project starts with, which its variants switch and tune differently
GEOMETRY_STEPS: tuple[str, ...] = (
    'geometry.perspective',
    'geometry.deskew',
    'geometry.dewarp',
    'geometry.crop',
    'geometry.normalize',
)


async def find_step(recipes: RecipeRepository, project_id: ProjectId, key: PageStepKey) -> Step:
    """Find a step by its identifier in the recipes of its stage, where a copy of a step keeps the identifier.

    :param recipes: Repository to read the recipes from.
    :type recipes: RecipeRepository
    :param project_id: Project owning the recipes.
    :type project_id: ProjectId
    :param key: The page, the stage and the step.
    :type key: PageStepKey
    :returns: The step as the first recipe of the stage that has it, the active one first, holds it.
    :rtype: Step
    :raises NotFoundError: If no recipe of the stage has the step.
    """
    for recipe in await recipes.list_for_stage(project_id, key.stage):
        for step in recipe.steps:
            if step.step_id == key.step_id:
                return step
    raise NotFoundError(key.step_id)


@frozen(kw_only=True)
class RecipeTemplate:
    """A recipe a stage starts with.

    :ivar name: Name the user sees.
    :ivar processor_keys: Keys of the processors of the steps, which run with their default parameters unless the
                          template gives others.
    :ivar params: Parameters of some of the steps, by the key of the processor, which the defaults fill out.
    :ivar off: Keys of the processors whose steps are in the recipe but switched off.
    :ivar condition: The pages that are sent to this recipe from the start, by a rule of the stage, or None for a recipe
                     no rule sends pages to.
    """

    name: str
    processor_keys: tuple[str, ...]
    params: Mapping[str, MetadataMap] = field(factory=dict)
    off: frozenset[str] = frozenset()
    condition: RuleCondition | None = None


# The steps of the Cleanup stage in the order they run: the page is made black and white first, so that what is left
# of the dust is separate small spots, which are then removed, and the user's eraser comes last
BINARIZE_KEY: str = 'cleanup.binarize'
DESPECKLE_KEY: str = 'cleanup.despeckle'
ERASER_KEY: str = 'cleanup.eraser'
# The parameters the templates of the stage set
MODE_PARAM: str = 'mode'
METHOD_PARAM: str = 'method'
STRENGTH_PARAM: str = 'strength'


class DefaultRecipes:
    """The recipes of each stage that a project starts with, the first of a stage being its active one."""

    TEMPLATES: ClassVar[Mapping[Stage, tuple[RecipeTemplate, ...]]] = {
        Stage.PAGE_SPLIT: (
            RecipeTemplate(name='Automatic', processor_keys=('split.auto',)),
            RecipeTemplate(name='Whole scan', processor_keys=('split.none',)),
            RecipeTemplate(name='Spread', processor_keys=('split.spread',)),
        ),
        Stage.GEOMETRY: (
            RecipeTemplate(
                name='Text',
                processor_keys=GEOMETRY_STEPS,
                params={
                    'geometry.deskew': {'method': DeskewMethod.PROJECTION},
                    'geometry.dewarp': {'method': DewarpMethod.TEXT_LINES},
                },
            ),
            RecipeTemplate(
                name='Plates',
                processor_keys=GEOMETRY_STEPS,
                params={
                    'geometry.deskew': {'method': DeskewMethod.HOUGH},
                    'geometry.dewarp': {'method': DewarpMethod.PAGE_EDGES},
                },
                condition=RuleCondition.PLATES,
            ),
            RecipeTemplate(
                name='Flat',
                processor_keys=GEOMETRY_STEPS,
                off=frozenset({'geometry.dewarp'}),
            ),
        ),
        Stage.CLEANUP: (
            RecipeTemplate(
                name='Text',
                processor_keys=(BINARIZE_KEY, DESPECKLE_KEY, ERASER_KEY),
                params={
                    BINARIZE_KEY: {MODE_PARAM: OutputMode.BW, METHOD_PARAM: BinarizationMethod.SAUVOLA},
                    DESPECKLE_KEY: {STRENGTH_PARAM: 2},
                },
            ),
            RecipeTemplate(
                name='Plates',
                processor_keys=(BINARIZE_KEY, ERASER_KEY),
                params={BINARIZE_KEY: {MODE_PARAM: OutputMode.GRAY, METHOD_PARAM: BinarizationMethod.SAUVOLA}},
                condition=RuleCondition.PLATES,
            ),
            RecipeTemplate(
                name='Mixed',
                processor_keys=(BINARIZE_KEY, DESPECKLE_KEY, ERASER_KEY),
                params={
                    BINARIZE_KEY: {MODE_PARAM: OutputMode.MIXED, METHOD_PARAM: BinarizationMethod.SAUVOLA},
                    DESPECKLE_KEY: {STRENGTH_PARAM: 1},
                },
                condition=RuleCondition.ILLUSTRATED,
            ),
        ),
    }

    def __init__(self, templates: Mapping[Stage, tuple[RecipeTemplate, ...]] | None = None) -> None:
        """Start from the given templates, or from the ones every project gets.

        :param templates: Templates by stage, the first of a stage being its active recipe, or None for ``TEMPLATES``.
        :type templates: Mapping[Stage, tuple[RecipeTemplate, ...]] | None
        """
        self._templates = self.TEMPLATES if templates is None else templates

    def for_stage(self, stage: Stage) -> Sequence[RecipeTemplate]:
        """Return the templates of a stage, the active one first.

        :param stage: The stage.
        :type stage: Stage
        :returns: The templates, none for a stage that has no recipe by default.
        :rtype: Sequence[RecipeTemplate]
        """
        return self._templates.get(stage, ())


class RecipeBook:
    """Finds the recipes of a project, creates the default ones, and checks the steps of a recipe."""

    def __init__(self, *, uow: UnitOfWork, catalogue: ProcessorCatalog, defaults: DefaultRecipes, clock: Clock) -> None:
        """Work over the ports of one request or job.

        :param uow: Unit of work, whose commit ends the use case.
        :type uow: UnitOfWork
        :param catalogue: The processors the application can run.
        :type catalogue: ProcessorCatalog
        :param defaults: The recipes a stage starts with.
        :type defaults: DefaultRecipes
        :param clock: Clock stamping new recipes.
        :type clock: Clock
        """
        self._uow = uow
        self._catalogue = catalogue
        self._defaults = defaults
        self._clock = clock

    async def active(self, project_id: ProjectId, stage: Stage) -> Recipe:
        """Return the active recipe of a stage, creating the default recipes of the stage the first time.

        :param project_id: Project owning the recipe.
        :type project_id: ProjectId
        :param stage: The stage.
        :type stage: Stage
        :returns: The active recipe.
        :rtype: Recipe
        :raises NotFoundError: If the stage has no recipe by default, or none of its processors is installed.
        """
        if (found := await self._uow.recipes.find_active(project_id, stage)) is not None:
            return found
        moment = self._clock.now()
        buildable = [
            template
            for template in self._defaults.for_stage(stage)
            if all(self._offers(key) for key in template.processor_keys)
        ]
        preferred = await self._default_profile_recipe(project_id, stage, moment)
        if not buildable and preferred is None:
            raise NotFoundError(NO_RECIPE.format(stage=stage.label))
        built = [
            Recipe(
                id=RecipeId(uuid4()),
                project_id=project_id,
                stage=stage,
                name=template.name,
                steps=tuple(
                    Step(
                        processor_key=key,
                        params=self._catalogue.get(key).validate_params(template.params.get(key, {})),
                        enabled=key not in template.off,
                    )
                    for key in template.processor_keys
                ),
                active=index == 0 and preferred is None,
                # A microsecond apart and after the recipe of a default profile, so the recipes are listed in the order
                # of their templates
                created_at=moment + timedelta(microseconds=index + 1),
                updated_at=moment,
            )
            for index, template in enumerate(buildable)
        ]
        recipes = built if preferred is None else [preferred, *built]
        try:
            await self._uow.recipes.add_many(recipes)
            targeted = [
                (recipe, template.condition)
                for recipe, template in zip(built, buildable, strict=True)
                if template.condition is not None
            ]
            for order, (recipe, condition) in enumerate(targeted):
                rule = RecipeRule(
                    id=RecipeRuleId(uuid4()),
                    project_id=project_id,
                    stage=stage,
                    condition=condition,
                    recipe_id=recipe.id,
                    order=order,
                )
                await self._uow.recipe_rules.add(rule)
            await self._uow.commit()
        except ConflictError:
            # A request that ran at the same time stored the recipes first, and its recipes are the ones to use
            await self._uow.rollback()
            if (found := await self._uow.recipes.find_active(project_id, stage)) is None:
                raise
            return found
        return recipes[0]

    async def get(self, project_id: ProjectId, recipe_id: RecipeId, *, stage: Stage | None = None) -> Recipe:
        """Return a recipe of the project, of the given stage when one is named.

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
        recipe = await self._uow.recipes.get(recipe_id)
        if recipe.project_id != project_id or (stage is not None and recipe.stage is not stage):
            raise NotFoundError(recipe_id)
        return recipe

    async def add_variant(self, project_id: ProjectId, stage: Stage, name: str, steps: Sequence[Step]) -> Recipe:
        """Store a recipe of a stage that is not active.

        :param project_id: Project owning the recipe.
        :type project_id: ProjectId
        :param stage: The stage.
        :type stage: Stage
        :param name: Name of the variant.
        :type name: str
        :param steps: Steps of the variant, which are checked against their processors.
        :type steps: Sequence[Step]
        :returns: The variant as stored, not yet committed.
        :rtype: Recipe
        :raises NotFoundError: If the stage has no recipe.
        :raises InvalidParametersError: If a step does not fit its processor.
        """
        await self.active(project_id, stage)
        moment = self._clock.now()
        variant = Recipe(
            id=RecipeId(uuid4()),
            project_id=project_id,
            stage=stage,
            name=name,
            steps=await self.check(stage, steps),
            created_at=moment,
            updated_at=moment,
        )
        await self._uow.recipes.add(variant)
        return variant

    async def rewrite(self, recipe: Recipe, name: str, steps: Sequence[Step]) -> Recipe:
        """Store a new name and new steps of a recipe.

        :param recipe: The recipe to change.
        :type recipe: Recipe
        :param name: New name.
        :type name: str
        :param steps: New steps, which are checked against their processors.
        :type steps: Sequence[Step]
        :returns: The recipe as stored, not yet committed.
        :rtype: Recipe
        :raises InvalidParametersError: If a step does not fit its processor.
        """
        changed = evolve(recipe, name=name, steps=await self.check(recipe.stage, steps), updated_at=self._clock.now())
        await self._uow.recipes.update(changed)
        return changed

    async def switch_active(self, previous: Recipe, chosen: Recipe) -> Recipe:
        """Make a variant the active recipe of its stage, deactivating the active one first.

        The old recipe is deactivated before the variant is activated, so a stage never has two active recipes.

        :param previous: The active recipe of the stage.
        :type previous: Recipe
        :param chosen: The variant of the same stage to activate.
        :type chosen: Recipe
        :returns: The recipe as active, not yet committed.
        :rtype: Recipe
        """
        moment = self._clock.now()
        await self._uow.recipes.update(evolve(previous, active=False, updated_at=moment))
        activated = evolve(chosen, active=True, updated_at=moment)
        await self._uow.recipes.update(activated)
        return activated

    async def check(self, stage: Stage, steps: Sequence[Step]) -> tuple[Step, ...]:
        """Check the steps of a recipe of a stage and return them with their parameters in checked form.

        :param stage: Stage the recipe processes.
        :type stage: Stage
        :param steps: The steps as a user gave them.
        :type steps: Sequence[Step]
        :returns: The steps with the defaults of each processor filled in.
        :rtype: tuple[Step, ...]
        :raises InvalidParametersError: If there is no step or every step is switched off, a processor does not exist
                                        or belongs to another stage, or a parameter is wrong.
        """
        if not steps:
            raise InvalidParametersError(NO_STEPS)
        if not any(step.enabled for step in steps):
            raise InvalidParametersError(ALL_STEPS_OFF)
        return tuple([await self._check_step(stage, step) for step in steps])

    async def _check_step(self, stage: Stage, step: Step) -> Step:
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

    async def _default_profile_recipe(self, project_id: ProjectId, stage: Stage, moment: datetime) -> Recipe | None:
        """Build the active recipe of a stage from the default profile of the project's owner, if there is a usable one.

        A step whose processor is not installed is left out. A profile that has no step left to run, or whose steps no
        longer fit their processors, is passed over, so the stage starts with the built-in recipes as it does for an
        account without a default profile.

        :param project_id: Project the recipe is for.
        :type project_id: ProjectId
        :param stage: The stage.
        :type stage: Stage
        :param moment: The time the recipe is created at.
        :type moment: datetime
        :returns: The recipe, not stored yet, or None.
        :rtype: Recipe | None
        """
        owner_id = (await self._uow.projects.get(project_id)).owner_id
        if (profile := await self._uow.recipe_profiles.find_default(owner_id, stage)) is None:
            return None
        try:
            steps = await self.check(stage, self.installed(profile.steps)[0])
        except InvalidParametersError:
            return None
        return Recipe(
            id=RecipeId(uuid4()),
            project_id=project_id,
            stage=stage,
            name=profile.name,
            steps=steps,
            active=True,
            created_at=moment,
            updated_at=moment,
        )

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
