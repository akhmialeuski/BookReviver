"""Schemas of the recipe profiles of an account: the steps of a recipe saved to apply to other books.

A profile is saved from the steps on the screen, so its body is the body of a recipe with the stage added. Applying a
profile to a book answers with the recipe it made and the processors whose steps had to be left out. A profile is
exchanged as a file, whose format has a version, so a later change of the format can still read an older file.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self

from fastapi import Query
from fastapi_pagination import Params
from pydantic import Field

from bookreviver.api.schemas.base import RequestModel, ResponseModel
from bookreviver.api.schemas.jobs import JobSchema
from bookreviver.api.schemas.processing import RecipeBody, RecipeSchema, StepSchema
from bookreviver.api.schemas.types import RECIPE_STEPS_MAX_LENGTH, PageIdList, RecipeName
from bookreviver.domain.enums import AppliesTo, OrderMode, ProfileFileVersion, Stage
from bookreviver.domain.ids import RecipeProfileId
from bookreviver.domain.values import RecipeDraft, Step

if TYPE_CHECKING:
    from bookreviver.domain.entities import RecipeProfile
    from bookreviver.services.recipe_profiles import ListedProfile


class RecipeProfileSchema(ResponseModel):
    """A recipe profile of the signed-in account.

    :ivar id: Identifier of the profile.
    :ivar stage: Stage whose recipes the profile can be applied to.
    :ivar name: Name the user sees.
    :ivar steps: The steps in the order they run, each with its parameters and its switch.
    :ivar order: ``usual`` if the steps were saved with a step off a required place refused, ``free`` if it may stand.
    :ivar is_default: Whether a new book starts the stage with this profile.
    :ivar created_at: When the profile was saved.
    :ivar updated_at: When the profile was last changed.
    """

    id: RecipeProfileId
    stage: Stage
    name: str
    steps: list[StepSchema]
    order: OrderMode
    is_default: bool
    created_at: datetime
    updated_at: datetime


class LibraryProfileSchema(RecipeProfileSchema):
    """A recipe profile of the signed-in account with the number of books that use it.

    :ivar books: Number of books that have a recipe made from the profile.
    """

    books: int

    @classmethod
    def of(cls, listed: ListedProfile) -> Self:
        """Build the schema of a profile of the library.

        :param listed: The profile with its books.
        :type listed: ListedProfile
        :returns: The schema.
        :rtype: Self
        """
        profile = RecipeProfileSchema.model_validate(listed.profile)
        return cls.model_validate({**profile.model_dump(), 'books': listed.books})


class RecipeProfileBody(RecipeBody):
    """The name and the steps of a profile to save, with the stage whose recipes it fits.

    :ivar name: Name of the profile.
    :ivar steps: Its steps in the order they run, each checked against its processor.
    :ivar order: The order the profile is saved in, which a book opened from it starts in.
    :ivar stage: Stage whose recipes the profile can be applied to.
    """

    stage: Stage


class RecipeProfileName(RequestModel):
    """The new name of a profile.

    :ivar name: Name of the profile.
    """

    name: RecipeName


class ProfileQuery(Params):
    """The query of the list of profiles: the page parameters, and the stage to list.

    :ivar page: Number of the page of the list, from one.
    :ivar size: Number of profiles in one page of the list.
    :ivar stage: Stage whose profiles are listed, or omitted for every stage.
    """

    stage: Stage | None = Query(default=None, description='List the profiles of this stage only')


class ApplyProfileBody(RequestModel):
    """How to apply a profile to a book.

    :ivar activate: Whether the new variant also becomes the active recipe of the stage.
    :ivar page_ids: The pages to pin the new variant to and to run the stage on, or omitted to apply the profile to the
                    book only.
    """

    activate: bool = False
    page_ids: PageIdList | None = None


class AppliedProfileSchema(ResponseModel):
    """The recipe a profile made in a book, and what was left out of it.

    :ivar recipe: The variant added to the book, which is the active recipe when the request asked for that.
    :ivar missing_processors: Keys of the processors of the profile that are not installed, whose steps were left out.
    :ivar job: The queued run of the stage on the pages the profile was applied to, or null when it was applied to the
               book only.
    """

    recipe: RecipeSchema
    missing_processors: list[str]
    job: JobSchema | None = None


class ProfileLinkBody(RequestModel):
    """The profile a recipe of a book was made from.

    :ivar profile_id: Identifier of the profile, or null to say the recipe was made from none.
    """

    profile_id: RecipeProfileId | None


class ProfileFileStep(RequestModel):
    """One step of a profile file: a processor, its parameters, whether it is on, and the pages it runs on.

    The step has no identifier, since the identifier belongs to the recipe a step is in, and an import gives each step a
    new one.

    :ivar processor_key: Key of the processor.
    :ivar params: Parameters of the step, which an import checks against the processor.
    :ivar enabled: Whether a run runs the step.
    :ivar applies_to: Which pages the step processes.
    """

    processor_key: Annotated[str, Field(min_length=1)]
    params: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    applies_to: AppliesTo = AppliesTo.ALL


class ProfileFileSchema(RequestModel):
    """A profile as a file that is exchanged between accounts: the answer of an export, and the body of an import.

    :ivar version: Version of the format of the file.
    :ivar stage: Stage whose recipes the profile can be applied to.
    :ivar name: Name of the profile.
    :ivar order: ``usual`` if the profile refuses a step off a required place, ``free`` if it lets it stand.
    :ivar steps: The steps in the order they run.
    """

    version: Literal[ProfileFileVersion.V1]
    stage: Stage
    name: RecipeName
    order: OrderMode = OrderMode.USUAL
    steps: Annotated[list[ProfileFileStep], Field(min_length=1, max_length=RECIPE_STEPS_MAX_LENGTH)]

    @classmethod
    def of(cls, profile: RecipeProfile) -> Self:
        """Write a profile as a file.

        :param profile: The profile.
        :type profile: RecipeProfile
        :returns: The file.
        :rtype: Self
        """
        return cls(
            version=ProfileFileVersion.V1,
            stage=profile.stage,
            name=profile.name,
            order=profile.order,
            steps=[
                ProfileFileStep(
                    processor_key=step.processor_key,
                    params=dict(step.params),
                    enabled=step.enabled,
                    applies_to=step.applies_to,
                )
                for step in profile.steps
            ],
        )

    def to_draft(self) -> RecipeDraft:
        """Return the name, the steps and the order of the file as the domain states them.

        :returns: The draft, whose steps have new identifiers.
        :rtype: RecipeDraft
        """
        steps = [
            Step(processor_key=step.processor_key, params=step.params, enabled=step.enabled, applies_to=step.applies_to)
            for step in self.steps
        ]
        return RecipeDraft(name=self.name, steps=steps, order=self.order)
