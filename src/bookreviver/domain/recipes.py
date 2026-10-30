"""Recipes and recipe profiles: the ordered steps a stage runs on one kind of page."""

import enum
from typing import TYPE_CHECKING, Any, Self
from uuid import UUID, uuid4

from attrs import field, frozen, validators

from bookreviver.domain.ids import StepId
from bookreviver.domain.labels import LabeledStrEnum

if TYPE_CHECKING:
    from datetime import datetime

    from bookreviver.domain.ids import RecipeId, RecipeProfileId
    from bookreviver.domain.metadata import MetadataMap
    from bookreviver.domain.pages import RecipeKind
    from bookreviver.domain.stages import Stage


class OrderMode(LabeledStrEnum):
    """How strictly the order of the steps of a recipe is kept when the recipe is saved.

    The usual order refuses a step that stands where it cannot work, and the free order lets it stand, with a warning.
    A step that stands off its usual place is never refused, whatever the mode.
    """

    USUAL = 'usual', 'Usual order'
    FREE = 'free', 'Free order'


class ProfileFileVersion(enum.IntEnum):
    """The versions of the format a recipe profile is written to a file in, so a later format can read an older file.

    The second version has no condition on a step, which the first wrote as ``applies_to``.
    """

    V1 = 1
    V2 = 2


class StepField(LabeledStrEnum):
    """Keys of a step of a recipe in the JSON it is stored as, in a recipe row and in the parameters of a job."""

    PROCESSOR_KEY = 'processor_key', 'Key of the processor that runs the step'
    PARAMS = 'params', 'Parameters the processor runs with'
    ENABLED = 'enabled', 'Whether a run and a preview run the step'
    STEP_ID = 'step_id', 'Identifier of the step, which stays as the step moves and is saved'


@frozen(kw_only=True)
class Step:
    """One step of a recipe: a processor and the parameters it runs with.

    The processor key is not unique in a recipe, since a processor may be added twice with other parameters, so the step
    has an identifier of its own. A manual edit belongs to it, and it stays as the step is moved, saved or kept in a
    profile.

    :ivar processor_key: Key of the processor, such as ``geometry.deskew``.
    :ivar params: Parameters of the step, following the processor's JSON Schema.
    :ivar enabled: Whether a run and a preview run the step. A step that is off stays in the recipe with its parameters,
                   so switching it on again loses nothing.
    :ivar step_id: Identifier of the step, made when the step is added.
    """

    processor_key: str = field(validator=validators.min_len(1))
    params: MetadataMap = field(factory=dict)
    enabled: bool = True
    step_id: StepId = field(factory=lambda: StepId(uuid4()))

    def to_map(self) -> dict[str, Any]:
        """Return the step as the JSON object a recipe and the parameters of a job store.

        :returns: The processor key, the parameters, whether the step is on and its identifier.
        :rtype: dict[str, Any]
        """
        return {
            StepField.PROCESSOR_KEY: self.processor_key,
            StepField.PARAMS: dict(self.params),
            StepField.ENABLED: self.enabled,
            StepField.STEP_ID: str(self.step_id),
        }

    @classmethod
    def from_map(cls, stored: MetadataMap) -> Self:
        """Read the step from the JSON object ``to_map`` wrote.

        A step stored before the switch existed has no ``enabled`` key and is on, as every step was. One stored before
        the identifier existed has no ``step_id`` and gets a new one. The migration of the recipes gives the stored
        steps their identifiers, so only the parameters of a job queued before it can lack one.

        :param stored: The stored object.
        :type stored: MetadataMap
        :returns: The step.
        :rtype: Self
        :raises KeyError: If the object has no processor key or no parameters.
        :raises ValueError: If the identifier is not valid.
        """
        step_id = stored.get(StepField.STEP_ID)
        return cls(
            processor_key=stored[StepField.PROCESSOR_KEY],
            params=stored[StepField.PARAMS],
            enabled=stored.get(StepField.ENABLED, True),
            step_id=StepId(uuid4() if step_id is None else UUID(step_id)),
        )


def _steps_tuple(steps: list[Step] | tuple[Step, ...]) -> tuple[Step, ...]:
    """Keep the steps of a draft as a tuple, whatever sequence they were given in.

    attrs reads the signature of a converter when it builds the class, so the annotation names only types that exist
    at runtime.

    :param steps: The steps.
    :type steps: list[Step] | tuple[Step, ...]
    :returns: The steps as a tuple.
    :rtype: tuple[Step, ...]
    """
    return tuple(steps)


@frozen(kw_only=True)
class RecipeDraft:
    """The steps of a recipe as a user gives them, with the order they are to be kept in.

    :ivar steps: The steps in the order they run, not yet checked against their processors.
    :ivar order: Whether a step that stands where it cannot work is refused or only warned of.
    :ivar profile_id: The profile the steps come from, which a recipe made from the draft is linked to. A recipe that
                      is rewritten keeps the link it has, and a profile ignores it.
    """

    steps: tuple[Step, ...] = field(converter=_steps_tuple)
    order: OrderMode = OrderMode.USUAL
    profile_id: RecipeProfileId | None = None


@frozen(kw_only=True)
class ProfileDraft(RecipeDraft):
    """The name and the steps of a recipe profile as a user gives them.

    :ivar name: Name the user sees.
    """

    name: str = field(validator=validators.min_len(1))


@frozen
class RecipeKey:
    """The address of a recipe: the stage it belongs to and its identifier, which a use case takes as one value.

    :ivar stage: The stage of the recipe.
    :ivar recipe_id: Identifier of the recipe.
    """

    stage: Stage
    recipe_id: RecipeId


@frozen(kw_only=True)
class Recipe:
    """The ordered steps of one stage for one kind of page, saved in a book.

    A stage that has recipes has exactly one for each ``RecipeKind``, and a page is processed by the recipe of its kind.
    The name of a recipe is the label of its kind.

    :ivar id: Identifier of the recipe.
    :ivar stage: Stage the recipe processes.
    :ivar kind: Kind of the pages the recipe processes, which is unique among the recipes of the stage.
    :ivar steps: The steps in the order they run, each a processor with its parameters.
    :ivar profile_id: The profile of the book the recipe was made from, or None for a recipe that was not. The steps
                      may have changed since, and the link survives that, so the book can tell how it differs from
                      the profile. Deleting the profile clears it.
    :ivar created_at: When the recipe was created.
    :ivar updated_at: When the recipe was last changed.
    """

    id: RecipeId
    stage: Stage
    kind: RecipeKind
    steps: tuple[Step, ...]
    profile_id: RecipeProfileId | None = None
    created_at: datetime
    updated_at: datetime

    @property
    def enabled_steps(self) -> tuple[Step, ...]:
        """The steps a run and a preview run, in order, which leaves out the ones switched off."""
        return tuple(step for step in self.steps if step.enabled)

    def place_of(self, step_id: StepId) -> int | None:
        """Give the place of a step among the steps that are on, which is where its version stands in a chain.

        Every step that is on stores one version that reads the one before it, so the place of a step in the chain of a
        page is the number of steps that are on before it.

        :param step_id: The step.
        :type step_id: StepId
        :returns: The place, from zero, or None when the recipe has no such step or has it switched off.
        :rtype: int | None
        """
        return next((place for place, step in enumerate(self.enabled_steps) if step.step_id == step_id), None)

    def indexed_steps_through(self, through_step: int | None) -> tuple[tuple[int, Step], ...]:
        """Give the steps that are on up to one of them, each with its index in the recipe.

        :param through_step: Index of the last step to take, or None to take every step that is on.
        :type through_step: int | None
        :returns: The pairs of index and step in the order they run. Empty when every step up to the index is off.
        :rtype: tuple[tuple[int, Step], ...]
        """
        return tuple(
            (index, step)
            for index, step in enumerate(self.steps)
            if step.enabled and (through_step is None or index <= through_step)
        )

    def stopped_at(self, through_step: int | None) -> int | None:
        """Tell where a run through one step stops when that is short of the last step that is on.

        :param through_step: Index of the last step to run, or None for every step that is on.
        :type through_step: int | None
        :returns: The index of the last step that run makes, or None when it makes the last step that is on, or none.
        :rtype: int | None
        """
        reached = self.indexed_steps_through(through_step)
        every = self.indexed_steps_through(None)
        if not reached or reached[-1] == every[-1]:
            return None
        return reached[-1][0]


@frozen(kw_only=True)
class RecipeProfile:
    """The steps of a recipe of one stage that are saved in a book, to apply to its recipes.

    A profile belongs to one book, like the recipes made from it, so a book that is set up once can be set up again by
    applying the profile to another recipe of it. A profile reaches another book only as a file. The steps keep their
    order, their parameters and their ``enabled`` switch.

    :ivar id: Identifier of the profile.
    :ivar stage: Stage whose recipes the profile can be applied to.
    :ivar name: Name the user sees, such as ``Photographed book``.
    :ivar steps: The steps in the order they run, each a processor with its parameters.
    :ivar order: Whether the steps were saved in the usual order, which refuses a step where it cannot work, or in the
                 free order, which lets it stand with a warning. A recipe made from the profile starts in this mode.
    :ivar created_at: When the profile was saved.
    :ivar updated_at: When the profile was last changed.
    """

    id: RecipeProfileId
    stage: Stage
    name: str = field(validator=validators.min_len(1))
    steps: tuple[Step, ...]
    order: OrderMode = OrderMode.USUAL
    created_at: datetime
    updated_at: datetime
