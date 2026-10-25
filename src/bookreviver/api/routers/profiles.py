"""The recipe profiles of a book: saving the steps of a recipe, applying them to a recipe of the book.

A profile lives in its book, so every route here opens the book first, and a profile the book does not have answers 404
like a missing one. Applying a profile runs nothing and processes no page; it only puts the steps into the recipe of
one kind of page, which marks the pages the recipe processed stale. A profile reaches another book as a file, through
the export and the import. The dependency of ``bookreviver.api.access`` opens the book, so the service is handed what it
works on.
"""

from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends, Path, status
from fastapi_pagination import Page

from bookreviver.api.access import open_book
from bookreviver.api.freshness import fresh_book
from bookreviver.api.pagination import Pager
from bookreviver.api.routers.processing import RecipePath
from bookreviver.api.schemas.processing import RecipeSchema
from bookreviver.api.schemas.profiles import (
    AppliedProfileSchema,
    ApplyProfileBody,
    ProfileBody,
    ProfileFile,
    ProfileFileSchema,
    ProfileLinkBody,
    ProfileQuery,
    RecipeProfileBody,
    RecipeProfileName,
    RecipeProfileSchema,
)
from bookreviver.domain.entities import RecipeProfile
from bookreviver.domain.ids import RecipeProfileId
from bookreviver.domain.values import RecipeKey
from bookreviver.services.book import Book
from bookreviver.services.recipe_order import RecipeOrder
from bookreviver.services.recipe_profiles import RecipeProfiles

PROFILE_ID_DESCRIPTION: str = 'Identifier of the recipe profile'
PROFILES_PATH: str = '/{book_id}/recipe-profiles'
PROFILE_PATH: str = PROFILES_PATH + '/{profile_id}'
DUPLICATE_PATH: str = PROFILE_PATH + '/duplicate'
EXPORT_PATH: str = PROFILE_PATH + '/export'
IMPORT_PATH: str = PROFILES_PATH + '/import'
APPLY_PATH: str = PROFILE_PATH + '/apply'
LINK_PATH: str = '/{book_id}/stages/{stage}/recipes/{recipe_id}/profile'

router = APIRouter(prefix='/books', tags=['profiles'], route_class=DishkaRoute, dependencies=[Depends(fresh_book)])


@router.get(PROFILES_PATH)
async def list_profiles(
    book: Annotated[Book, Depends(open_book)],
    query: Annotated[ProfileQuery, Depends()],
    profiles: FromDishka[RecipeProfiles],
) -> Page[RecipeProfileSchema]:
    """List the profiles of a book in the order of the stages, then oldest first.

    \N{FORM FEED}
    :param book: The book the path names, open for the signed-in account.
    :type book: Book
    :param query: Page number and size from the query, and the stage to list.
    :type query: ProfileQuery
    :param profiles: Profiles service of the request.
    :type profiles: RecipeProfiles
    :returns: One page of the profiles.
    :rtype: Page[RecipeProfileSchema]
    """
    pager = Pager[RecipeProfile, RecipeProfileSchema](query, RecipeProfileSchema.model_validate)
    return pager.page(await profiles.library(book, query.stage, pager.request))


@router.post(PROFILES_PATH, status_code=status.HTTP_201_CREATED)
async def create_profile(
    book: Annotated[Book, Depends(open_book)], body: RecipeProfileBody, profiles: FromDishka[RecipeProfiles]
) -> RecipeProfileSchema:
    """Save the steps of a recipe as a profile of the book.

    A step whose processor is unknown or of another stage, or whose parameters do not fit, answers 422, and so does a
    step that stands where it cannot work, unless the body asks for the free order.

    \N{FORM FEED}
    :param book: The book the path names, open for the signed-in account.
    :type book: Book
    :param body: The stage, the name, the steps and the order to keep.
    :type body: RecipeProfileBody
    :param profiles: Profiles service of the request.
    :type profiles: RecipeProfiles
    :returns: The profile as stored.
    :rtype: RecipeProfileSchema
    """
    profile = await profiles.save(book, body.stage, body.to_draft())
    return RecipeProfileSchema.model_validate(profile)


@router.post(IMPORT_PATH, status_code=status.HTTP_201_CREATED)
async def import_profile(
    book: Annotated[Book, Depends(open_book)], body: ProfileFile, profiles: FromDishka[RecipeProfiles]
) -> RecipeProfileSchema:
    """Save the profile a file holds as a profile of the book.

    The file is checked as a saved profile is. A file of the first version is read as well, and the condition its
    steps name is dropped. A file of another version of the format, a step whose parameters do not
    fit, and a step that stands where it cannot work, unless the file asks for the free order, answer 422, and so does
    a file that needs a processor that is not installed, which the answer names.

    \N{FORM FEED}
    :param book: The book the path names, open for the signed-in account.
    :type book: Book
    :param body: The content of the profile file, of the version it says.
    :type body: ProfileFile
    :param profiles: Profiles service of the request.
    :type profiles: RecipeProfiles
    :returns: The profile as stored.
    :rtype: RecipeProfileSchema
    """
    profile = await profiles.import_profile(book, body.stage, body.to_draft())
    return RecipeProfileSchema.model_validate(profile)


@router.get(EXPORT_PATH)
async def export_profile(
    book: Annotated[Book, Depends(open_book)],
    profile_id: Annotated[RecipeProfileId, Path(description=PROFILE_ID_DESCRIPTION)],
    profiles: FromDishka[RecipeProfiles],
) -> ProfileFileSchema:
    """Write a profile as a file, which another book can import.

    \N{FORM FEED}
    :param book: The book the path names, open for the signed-in account.
    :type book: Book
    :param profile_id: Identifier of the profile, from the path.
    :type profile_id: RecipeProfileId
    :param profiles: Profiles service of the request.
    :type profiles: RecipeProfiles
    :returns: The content of the profile file.
    :rtype: ProfileFileSchema
    """
    return ProfileFileSchema.of(await profiles.get(book, profile_id))


@router.post(DUPLICATE_PATH, status_code=status.HTTP_201_CREATED)
async def duplicate_profile(
    book: Annotated[Book, Depends(open_book)],
    profile_id: Annotated[RecipeProfileId, Path(description=PROFILE_ID_DESCRIPTION)],
    profiles: FromDishka[RecipeProfiles],
) -> RecipeProfileSchema:
    """Save a copy of a profile under its name with "(copy)" after it.

    \N{FORM FEED}
    :param book: The book the path names, open for the signed-in account.
    :type book: Book
    :param profile_id: Identifier of the profile to copy, from the path.
    :type profile_id: RecipeProfileId
    :param profiles: Profiles service of the request.
    :type profiles: RecipeProfiles
    :returns: The copy as stored.
    :rtype: RecipeProfileSchema
    """
    return RecipeProfileSchema.model_validate(await profiles.duplicate(book, profile_id))


@router.put(PROFILE_PATH)
async def put_profile(
    book: Annotated[Book, Depends(open_book)],
    profile_id: Annotated[RecipeProfileId, Path(description=PROFILE_ID_DESCRIPTION)],
    body: ProfileBody,
    profiles: FromDishka[RecipeProfiles],
) -> RecipeProfileSchema:
    """Replace the name, the steps and the order of a profile, which is how a book saves its changes to its profile.

    The stage stays. The recipes made from the profile are not changed. A step whose processor is
    unknown or of another stage, or whose parameters do not fit, answers 422, and so does a step that stands where it
    cannot work, unless the body asks for the free order.

    \N{FORM FEED}
    :param book: The book the path names, open for the signed-in account.
    :type book: Book
    :param profile_id: Identifier of the profile, from the path.
    :type profile_id: RecipeProfileId
    :param body: The new name, steps and order.
    :type body: ProfileBody
    :param profiles: Profiles service of the request.
    :type profiles: RecipeProfiles
    :returns: The profile as stored.
    :rtype: RecipeProfileSchema
    """
    replaced = await profiles.replace(book, profile_id, body.to_draft())
    return RecipeProfileSchema.model_validate(replaced)


@router.patch(PROFILE_PATH)
async def rename_profile(
    book: Annotated[Book, Depends(open_book)],
    profile_id: Annotated[RecipeProfileId, Path(description=PROFILE_ID_DESCRIPTION)],
    body: RecipeProfileName,
    profiles: FromDishka[RecipeProfiles],
) -> RecipeProfileSchema:
    """Give a profile another name.

    \N{FORM FEED}
    :param book: The book the path names, open for the signed-in account.
    :type book: Book
    :param profile_id: Identifier of the profile, from the path.
    :type profile_id: RecipeProfileId
    :param body: The new name.
    :type body: RecipeProfileName
    :param profiles: Profiles service of the request.
    :type profiles: RecipeProfiles
    :returns: The profile as stored.
    :rtype: RecipeProfileSchema
    """
    renamed = await profiles.rename(book, profile_id, body.name)
    return RecipeProfileSchema.model_validate(renamed)


@router.delete(PROFILE_PATH, status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(
    book: Annotated[Book, Depends(open_book)],
    profile_id: Annotated[RecipeProfileId, Path(description=PROFILE_ID_DESCRIPTION)],
    profiles: FromDishka[RecipeProfiles],
) -> None:
    """Delete a profile, which changes no step of any recipe and only clears the links to it.

    \N{FORM FEED}
    :param book: The book the path names, open for the signed-in account.
    :type book: Book
    :param profile_id: Identifier of the profile, from the path.
    :type profile_id: RecipeProfileId
    :param profiles: Profiles service of the request.
    :type profiles: RecipeProfiles
    """
    await profiles.remove(book, profile_id)


@router.post(APPLY_PATH, status_code=status.HTTP_201_CREATED)
async def apply_profile(
    book: Annotated[Book, Depends(open_book)],
    profile_id: Annotated[RecipeProfileId, Path(description=PROFILE_ID_DESCRIPTION)],
    body: ApplyProfileBody,
    profiles: FromDishka[RecipeProfiles],
    order: FromDishka[RecipeOrder],
) -> AppliedProfileSchema:
    """Put the steps of a profile into the recipe of one kind of page of the profile's stage in the book.

    A step whose processor is not installed on the server is left out and the processor is named in the answer. A
    profile with no step left to run answers 422. A profile the book does not have answers 404. The recipe is linked to
    the profile, and the pages it processed become stale.

    \N{FORM FEED}
    :param book: The book the path names, open for the signed-in account.
    :type book: Book
    :param profile_id: Identifier of the profile, from the path.
    :type profile_id: RecipeProfileId
    :param body: The kind of page whose recipe takes the steps.
    :type body: ApplyProfileBody
    :param profiles: Profiles service of the request.
    :type profiles: RecipeProfiles
    :param order: Finder of the steps that stand off the place their processors ask for.
    :type order: RecipeOrder
    :returns: The recipe, with the steps that are out of their place, and the processors whose steps were left out.
    :rtype: AppliedProfileSchema
    """
    applied = await profiles.apply(book, profile_id, kind=body.kind)
    return AppliedProfileSchema(
        recipe=RecipeSchema.of(applied.recipe, order.issues(applied.recipe.steps)),
        missing_processors=list(applied.missing_processors),
    )


@router.put(LINK_PATH)
async def put_recipe_profile(
    book: Annotated[Book, Depends(open_book)],
    address: Annotated[RecipePath, Depends()],
    body: ProfileLinkBody,
    profiles: FromDishka[RecipeProfiles],
    order: FromDishka[RecipeOrder],
) -> RecipeSchema:
    """Record which profile a recipe of a book was made from, so the book can tell how its steps differ from it.

    The steps do not change and no page goes stale. A profile the book does not have answers 404, and so does a recipe
    the book does not have, and a profile of another stage answers 422.

    \N{FORM FEED}
    :param book: The book the path names, open for the signed-in account.
    :type book: Book
    :param address: Identifiers of the book, the stage and the recipe.
    :type address: RecipePath
    :param body: The profile of the book, or null to unlink the recipe.
    :type body: ProfileLinkBody
    :param profiles: Profiles service of the request.
    :type profiles: RecipeProfiles
    :param order: Finder of the steps that stand off the place their processors ask for.
    :type order: RecipeOrder
    :returns: The recipe, with the steps that are out of their place.
    :rtype: RecipeSchema
    """
    linked = await profiles.link(book, RecipeKey(address.stage, address.recipe_id), body.profile_id)
    return RecipeSchema.of(linked, order.issues(linked.steps))
