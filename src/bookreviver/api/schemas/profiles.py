"""Schemas of the recipe profiles of an account: the steps of a recipe saved to apply to other books.

A profile is saved from the steps on the screen, so its body is the body of a recipe with the stage added. Applying a
profile to a book answers with the recipe it made and the processors whose steps had to be left out.
"""

from datetime import datetime

from fastapi import Query
from fastapi_pagination import Params

from bookreviver.api.schemas.base import RequestModel, ResponseModel
from bookreviver.api.schemas.processing import RecipeBody, RecipeSchema, StepSchema
from bookreviver.api.schemas.types import RecipeName
from bookreviver.domain.enums import OrderMode, Stage
from bookreviver.domain.ids import RecipeProfileId


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
    """

    activate: bool = False


class AppliedProfileSchema(ResponseModel):
    """The recipe a profile made in a book, and what was left out of it.

    :ivar recipe: The variant added to the book, which is the active recipe when the request asked for that.
    :ivar missing_processors: Keys of the processors of the profile that are not installed, whose steps were left out.
    """

    recipe: RecipeSchema
    missing_processors: list[str]


class ProfileLinkBody(RequestModel):
    """The profile a recipe of a book was made from.

    :ivar profile_id: Identifier of the profile, or null to say the recipe was made from none.
    """

    profile_id: RecipeProfileId | None
