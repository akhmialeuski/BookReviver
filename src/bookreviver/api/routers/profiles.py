"""The recipe profiles of the signed-in account: saving the steps of a recipe, applying them to a book, the default.

A profile is the account's, so these routes take no project except the ones that apply a profile to a book and that
record which profile a recipe of a book was made from. A profile of another account answers 404 like a missing one.
Applying a profile to a book runs nothing and processes no page; it only puts the steps into the recipe of one kind of
page, which marks the pages the recipe processed stale. A profile leaves the account and enters another as a
file, through the export and the import.
"""

from dataclasses import dataclass
from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends, Path, status
from fastapi_pagination import Page

from bookreviver.api.auth import ActorDep
from bookreviver.api.pagination import Pager
from bookreviver.api.routers.processing import PROJECT_ID_DESCRIPTION, RecipePath
from bookreviver.api.schemas.processing import RecipeSchema
from bookreviver.api.schemas.profiles import (
    AppliedProfileSchema,
    ApplyProfileBody,
    LibraryProfileSchema,
    ProfileBody,
    ProfileFile,
    ProfileFileSchema,
    ProfileLinkBody,
    ProfileQuery,
    RecipeProfileBody,
    RecipeProfileName,
    RecipeProfileSchema,
)
from bookreviver.domain.ids import ProjectId, RecipeProfileId
from bookreviver.domain.values import RecipeKey
from bookreviver.services.recipe_order import RecipeOrder
from bookreviver.services.recipe_profiles import ListedProfile, RecipeProfiles

PROFILE_ID_DESCRIPTION: str = 'Identifier of the recipe profile'
PROFILES_PATH: str = '/recipe-profiles'
PROFILE_PATH: str = PROFILES_PATH + '/{profile_id}'
DEFAULT_PATH: str = PROFILE_PATH + '/default'
DUPLICATE_PATH: str = PROFILE_PATH + '/duplicate'
EXPORT_PATH: str = PROFILE_PATH + '/export'
IMPORT_PATH: str = PROFILES_PATH + '/import'
APPLY_PATH: str = '/projects/{project_id}' + PROFILES_PATH + '/{profile_id}/apply'
LINK_PATH: str = '/projects/{project_id}/stages/{stage}/recipes/{recipe_id}/profile'

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
) -> Page[LibraryProfileSchema]:
    """List the profiles of the signed-in account in the order of the stages, then oldest first, with their books.

    The books of a profile are the books that have a recipe made from it.

    \N{FORM FEED}
    :param query: Page number and size from the query, and the stage to list.
    :type query: ProfileQuery
    :param actor: The signed-in account.
    :type actor: Actor
    :param profiles: Profiles service of the request.
    :type profiles: RecipeProfiles
    :returns: One page of the profiles.
    :rtype: Page[LibraryProfileSchema]
    """
    pager = Pager[ListedProfile, LibraryProfileSchema](query, LibraryProfileSchema.of)
    return pager.page(await profiles.library(actor, query.stage, pager.request))


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


@router.post(IMPORT_PATH, status_code=status.HTTP_201_CREATED)
async def import_profile(
    body: ProfileFile, actor: ActorDep, profiles: FromDishka[RecipeProfiles]
) -> RecipeProfileSchema:
    """Save the profile a file holds, which is not the default until it is made one.

    The file is checked as a saved profile is. A file of the first version is read as well, and the condition its
    steps name is dropped. A file of another version of the format, a step whose parameters do not
    fit, and a step that stands where it cannot work, unless the file asks for the free order, answer 422, and so does
    a file that needs a processor that is not installed, which the answer names.

    \N{FORM FEED}
    :param body: The content of the profile file, of the version it says.
    :type body: ProfileFile
    :param actor: The signed-in account.
    :type actor: Actor
    :param profiles: Profiles service of the request.
    :type profiles: RecipeProfiles
    :returns: The profile as stored.
    :rtype: RecipeProfileSchema
    """
    profile = await profiles.import_profile(actor, body.stage, body.to_draft())
    return RecipeProfileSchema.model_validate(profile)


@router.get(EXPORT_PATH)
async def export_profile(
    profile_id: ProfilePath, actor: ActorDep, profiles: FromDishka[RecipeProfiles]
) -> ProfileFileSchema:
    """Write a profile as a file, which another account can import.

    \N{FORM FEED}
    :param profile_id: Identifier of the profile.
    :type profile_id: RecipeProfileId
    :param actor: The signed-in account.
    :type actor: Actor
    :param profiles: Profiles service of the request.
    :type profiles: RecipeProfiles
    :returns: The content of the profile file.
    :rtype: ProfileFileSchema
    """
    return ProfileFileSchema.of(await profiles.get(actor, profile_id))


@router.post(DUPLICATE_PATH, status_code=status.HTTP_201_CREATED)
async def duplicate_profile(
    profile_id: ProfilePath, actor: ActorDep, profiles: FromDishka[RecipeProfiles]
) -> RecipeProfileSchema:
    """Save a copy of a profile under its name with "(copy)" after it, which is not the default.

    \N{FORM FEED}
    :param profile_id: Identifier of the profile to copy.
    :type profile_id: RecipeProfileId
    :param actor: The signed-in account.
    :type actor: Actor
    :param profiles: Profiles service of the request.
    :type profiles: RecipeProfiles
    :returns: The copy as stored.
    :rtype: RecipeProfileSchema
    """
    return RecipeProfileSchema.model_validate(await profiles.duplicate(actor, profile_id))


@router.put(PROFILE_PATH)
async def put_profile(
    profile_id: ProfilePath, body: ProfileBody, actor: ActorDep, profiles: FromDishka[RecipeProfiles]
) -> RecipeProfileSchema:
    """Replace the name, the steps and the order of a profile, which is how a book saves its changes to its profile.

    The stage and the default mark stay. The recipes made from the profile are not changed. A step whose processor is
    unknown or of another stage, or whose parameters do not fit, answers 422, and so does a step that stands where it
    cannot work, unless the body asks for the free order.

    \N{FORM FEED}
    :param profile_id: Identifier of the profile.
    :type profile_id: RecipeProfileId
    :param body: The new name, steps and order.
    :type body: ProfileBody
    :param actor: The signed-in account.
    :type actor: Actor
    :param profiles: Profiles service of the request.
    :type profiles: RecipeProfiles
    :returns: The profile as stored.
    :rtype: RecipeProfileSchema
    """
    replaced = await profiles.replace(actor, profile_id, body.to_draft())
    return RecipeProfileSchema.model_validate(replaced)


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
    """Put the steps of a profile into the recipe of one kind of page of the profile's stage in a book.

    A step whose processor is not installed on the server is left out and the processor is named in the answer. A
    profile with no step left to run answers 422. A profile of another account answers 404. The recipe is linked to the
    profile, and the pages it processed become stale.

    \N{FORM FEED}
    :param address: Identifiers of the project and the profile.
    :type address: AppliedPath
    :param body: The kind of page whose recipe takes the steps.
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
    applied = await profiles.apply(actor, address.project_id, address.profile_id, kind=body.kind)
    return AppliedProfileSchema(
        recipe=RecipeSchema.of(applied.recipe, order.issues(applied.recipe.steps)),
        missing_processors=list(applied.missing_processors),
    )


@router.put(LINK_PATH)
async def put_recipe_profile(
    address: Annotated[RecipePath, Depends()],
    body: ProfileLinkBody,
    actor: ActorDep,
    profiles: FromDishka[RecipeProfiles],
    order: FromDishka[RecipeOrder],
) -> RecipeSchema:
    """Record which profile a recipe of a book was made from, so the book can tell how its steps differ from it.

    The steps do not change and no page goes stale. A profile of another account answers 404, and so does a recipe the
    project does not have, and a profile of another stage answers 422.

    \N{FORM FEED}
    :param address: Identifiers of the project, the stage and the recipe.
    :type address: RecipePath
    :param body: The profile, or null to unlink the recipe.
    :type body: ProfileLinkBody
    :param actor: The signed-in account.
    :type actor: Actor
    :param profiles: Profiles service of the request.
    :type profiles: RecipeProfiles
    :param order: Finder of the steps that stand off the place their processors ask for.
    :type order: RecipeOrder
    :returns: The recipe, with the steps that are out of their place.
    :rtype: RecipeSchema
    """
    linked = await profiles.link(
        actor, address.project_id, RecipeKey(address.stage, address.recipe_id), body.profile_id
    )
    return RecipeSchema.of(linked, order.issues(linked.steps))
