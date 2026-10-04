"""The recipe profiles of the signed-in account: saving the steps of a recipe, applying them to a book, the default.

A profile is the account's, so these routes take no project except the one that applies a profile to a book. A profile
of another account answers 404 like a missing one. Applying a profile runs nothing and processes no page; like adding
a variant, it only stores a recipe, and making it the active one marks the pages the old active recipe processed stale.
"""

from dataclasses import dataclass
from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends, Path, status
from fastapi_pagination import Page

from bookreviver.api.auth import ActorDep
from bookreviver.api.pagination import Pager
from bookreviver.api.routers.processing import PROJECT_ID_DESCRIPTION
from bookreviver.api.schemas.processing import RecipeSchema
from bookreviver.api.schemas.profiles import (
    AppliedProfileSchema,
    ApplyProfileBody,
    ProfileQuery,
    RecipeProfileBody,
    RecipeProfileName,
    RecipeProfileSchema,
)
from bookreviver.domain.entities import RecipeProfile
from bookreviver.domain.ids import ProjectId, RecipeProfileId
from bookreviver.services.recipe_order import RecipeOrder
from bookreviver.services.recipe_profiles import RecipeProfiles

PROFILE_ID_DESCRIPTION: str = 'Identifier of the recipe profile'
PROFILES_PATH: str = '/recipe-profiles'
PROFILE_PATH: str = PROFILES_PATH + '/{profile_id}'
DEFAULT_PATH: str = PROFILE_PATH + '/default'
APPLY_PATH: str = '/projects/{project_id}' + PROFILES_PATH + '/{profile_id}/apply'

router = APIRouter(tags=['profiles'], route_class=DishkaRoute)

ProfilePath = Annotated[RecipeProfileId, Path(description=PROFILE_ID_DESCRIPTION)]


@dataclass(frozen=True)
class AppliedPath:
    """The identifiers in the address of a profile applied to a project.

    :ivar project_id: Identifier of the project.
    :ivar profile_id: Identifier of the profile.
    """

    project_id: Annotated[ProjectId, Path(description=PROJECT_ID_DESCRIPTION)]
    profile_id: ProfilePath


@router.get(PROFILES_PATH)
async def list_profiles(
    query: Annotated[ProfileQuery, Depends()], actor: ActorDep, profiles: FromDishka[RecipeProfiles]
) -> Page[RecipeProfileSchema]:
    """List the profiles of the signed-in account in the order of the stages, then oldest first.

    \N{FORM FEED}
    :param query: Page number and size from the query, and the stage to list.
    :type query: ProfileQuery
    :param actor: The signed-in account.
    :type actor: Actor
    :param profiles: Profiles service of the request.
    :type profiles: RecipeProfiles
    :returns: One page of the profiles.
    :rtype: Page[RecipeProfileSchema]
    """
    pager = Pager[RecipeProfile, RecipeProfileSchema](query, RecipeProfileSchema.model_validate)
    return pager.page(await profiles.profiles(actor, query.stage, pager.request))


@router.post(PROFILES_PATH, status_code=status.HTTP_201_CREATED)
async def create_profile(
    body: RecipeProfileBody, actor: ActorDep, profiles: FromDishka[RecipeProfiles]
) -> RecipeProfileSchema:
    """Save the steps of a recipe as a profile, which is not the default until it is made one.

    A step whose processor is unknown or of another stage, or whose parameters do not fit, answers 422, and so does a
    step that stands where it cannot work, unless the body asks for the free order.

    \N{FORM FEED}
    :param body: The stage, the name, the steps and the order to keep.
    :type body: RecipeProfileBody
    :param actor: The signed-in account.
    :type actor: Actor
    :param profiles: Profiles service of the request.
    :type profiles: RecipeProfiles
    :returns: The profile as stored.
    :rtype: RecipeProfileSchema
    """
    profile = await profiles.save(actor, body.stage, body.to_draft())
    return RecipeProfileSchema.model_validate(profile)


@router.patch(PROFILE_PATH)
async def rename_profile(
    profile_id: ProfilePath, body: RecipeProfileName, actor: ActorDep, profiles: FromDishka[RecipeProfiles]
) -> RecipeProfileSchema:
    """Give a profile another name.

    \N{FORM FEED}
    :param profile_id: Identifier of the profile.
    :type profile_id: RecipeProfileId
    :param body: The new name.
    :type body: RecipeProfileName
    :param actor: The signed-in account.
    :type actor: Actor
    :param profiles: Profiles service of the request.
    :type profiles: RecipeProfiles
    :returns: The profile as stored.
    :rtype: RecipeProfileSchema
    """
    renamed = await profiles.rename(actor, profile_id, body.name)
    return RecipeProfileSchema.model_validate(renamed)


@router.delete(PROFILE_PATH, status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(profile_id: ProfilePath, actor: ActorDep, profiles: FromDishka[RecipeProfiles]) -> None:
    """Delete a profile, which changes no recipe of any book.

    \N{FORM FEED}
    :param profile_id: Identifier of the profile.
    :type profile_id: RecipeProfileId
    :param actor: The signed-in account.
    :type actor: Actor
    :param profiles: Profiles service of the request.
    :type profiles: RecipeProfiles
    """
    await profiles.remove(actor, profile_id)


@router.put(DEFAULT_PATH)
async def put_default_profile(
    profile_id: ProfilePath, actor: ActorDep, profiles: FromDishka[RecipeProfiles]
) -> RecipeProfileSchema:
    """Make a profile the default of its stage, so a new book starts the stage with it instead of the built-in recipe.

    The profile that was the default of the stage stops being one.

    \N{FORM FEED}
    :param profile_id: Identifier of the profile.
    :type profile_id: RecipeProfileId
    :param actor: The signed-in account.
    :type actor: Actor
    :param profiles: Profiles service of the request.
    :type profiles: RecipeProfiles
    :returns: The profile as stored.
    :rtype: RecipeProfileSchema
    """
    return RecipeProfileSchema.model_validate(await profiles.set_default(actor, profile_id, is_default=True))


@router.delete(DEFAULT_PATH)
async def delete_default_profile(
    profile_id: ProfilePath, actor: ActorDep, profiles: FromDishka[RecipeProfiles]
) -> RecipeProfileSchema:
    """Stop a profile being the default of its stage, so a new book starts the stage with the built-in recipe again.

    \N{FORM FEED}
    :param profile_id: Identifier of the profile.
    :type profile_id: RecipeProfileId
    :param actor: The signed-in account.
    :type actor: Actor
    :param profiles: Profiles service of the request.
    :type profiles: RecipeProfiles
    :returns: The profile as stored.
    :rtype: RecipeProfileSchema
    """
    return RecipeProfileSchema.model_validate(await profiles.set_default(actor, profile_id, is_default=False))


@router.post(APPLY_PATH, status_code=status.HTTP_201_CREATED)
async def apply_profile(
    address: Annotated[AppliedPath, Depends()],
    body: ApplyProfileBody,
    actor: ActorDep,
    profiles: FromDishka[RecipeProfiles],
    order: FromDishka[RecipeOrder],
) -> AppliedProfileSchema:
    """Add the steps of a profile to a book as a variant of the profile's stage, and optionally make it the active one.

    A step whose processor is not installed on the server is left out and the processor is named in the answer. A
    profile with no step left to run answers 422. A profile of another account answers 404.

    \N{FORM FEED}
    :param address: Identifiers of the project and the profile.
    :type address: AppliedPath
    :param body: Whether the new variant becomes the active recipe.
    :type body: ApplyProfileBody
    :param actor: The signed-in account.
    :type actor: Actor
    :param profiles: Profiles service of the request.
    :type profiles: RecipeProfiles
    :param order: Finder of the steps that stand off the place their processors ask for.
    :type order: RecipeOrder
    :returns: The recipe, with the steps that are out of their place, and the processors whose steps were left out.
    :rtype: AppliedProfileSchema
    """
    applied = await profiles.apply(actor, address.project_id, address.profile_id, activate=body.activate)
    return AppliedProfileSchema(
        recipe=RecipeSchema.of(applied.recipe, order.issues(applied.recipe.steps)),
        missing_processors=list(applied.missing_processors),
    )
