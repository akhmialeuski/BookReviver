"""The recipe profiles of an account: saving, copying and exchanging the steps of a recipe, applying them to a book.

A profile belongs to the account. A recipe made from a profile remembers it (``Recipe.profile_id``), so the book can
tell how its steps differ from the profile, but it is a recipe like any other: deleting the profile only clears the
link and changes no book. Another account's profile is reported exactly like a missing one, as another account's
project is, so the identifiers of profiles cannot be probed.

Applying a profile to a book puts its steps into the recipe of one kind of page of the stage and links the recipe to the
profile, through the use cases of ``ProcessingService``, which also mark the pages the recipe processed stale. A
processor that is not installed on the server is left out of the recipe and named in the result, so one profile serves
several machines. An account has at most one default profile for each stage, whose steps the recipe of text pages
starts with at the first opening of the stage in a new book, through ``RecipeBook``.

A profile is exchanged as a file: the library exports one and imports another through the same checks that saving does.
An import refuses a file whose processors are not all installed, since a profile that cannot be applied whole would
be a profile the account holds without knowing it.
"""

from typing import TYPE_CHECKING
from uuid import uuid4

from attrs import evolve, frozen

from bookreviver.domain.entities import RecipeProfile
from bookreviver.domain.enums import OrderMode
from bookreviver.domain.errors import InvalidParametersError, NotFoundError
from bookreviver.domain.ids import RecipeProfileId
from bookreviver.domain.values import RecipeDraft, RecipeKey, Slice
from bookreviver.services.projects import owned_project

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor, Recipe
    from bookreviver.domain.enums import RecipeKind, Stage
    from bookreviver.domain.ids import ProjectId
    from bookreviver.domain.values import ProfileDraft, SliceRequest
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.runtime import Clock
    from bookreviver.services.processing import ProcessingService
    from bookreviver.services.recipes import RecipeBook

NOTHING_TO_APPLY: str = 'No step of the profile can run here, since no processor is installed for {keys}.'
OTHER_STAGE: str = 'The profile {name} is for the {actual} stage, not for the {expected} stage.'
MISSING_IN_FILE: str = 'The profile file needs processors that are not installed here: {keys}.'
COPY_NAME: str = '{name} (copy)'


@frozen(kw_only=True)
class ListedProfile:
    """A profile of the library with the number of books that use it.

    :ivar profile: The profile.
    :ivar books: Number of books that have a recipe made from the profile.
    """

    profile: RecipeProfile
    books: int


@frozen(kw_only=True)
class AppliedProfile:
    """The recipe a profile made in a book, and what was left out of it.

    :ivar recipe: The recipe of the book that took the steps of the profile.
    :ivar missing_processors: Keys of the processors of the profile that are not installed, whose steps were left out.
    """

    recipe: Recipe
    missing_processors: tuple[str, ...]


class RecipeProfiles:
    """Lists, saves, copies, exchanges, renames, deletes and applies the recipe profiles of the acting account."""

    def __init__(self, *, uow: UnitOfWork, recipes: RecipeBook, processing: ProcessingService, clock: Clock) -> None:
        """Work over the ports of one request.

        :param uow: Unit of work of the request. A use case that changes the profiles of the account runs in one
                    ``change`` block, and ``link`` runs in the ``change_book`` block of the book it changes.
        :type uow: UnitOfWork
        :param recipes: The recipes of the project, which check the steps of a profile.
        :type recipes: RecipeBook
        :param processing: The use cases that put the steps of a profile into a recipe of a book.
        :type processing: ProcessingService
        :param clock: Clock stamping new and changed profiles.
        :type clock: Clock
        """
        self._uow = uow
        self._recipes = recipes
        self._processing = processing
        self._clock = clock

    async def profiles(self, actor: Actor, stage: Stage | None, request: SliceRequest) -> Slice[RecipeProfile]:
        """List the profiles of the account in the order of the stages, then by creation.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param stage: The stage whose profiles are wanted, or None for every stage.
        :type stage: Stage | None
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :returns: The profiles of the window and the number of all that match.
        :rtype: Slice[RecipeProfile]
        """
        profiles = await self._uow.recipe_profiles.list_for_account(actor.account_id, stage)
        return Slice(items=profiles[request.offset : request.offset + request.limit], total=len(profiles))

    async def library(self, actor: Actor, stage: Stage | None, request: SliceRequest) -> Slice[ListedProfile]:
        """List the profiles of the account as ``profiles`` does, each with the number of books that use it.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param stage: The stage whose profiles are wanted, or None for every stage.
        :type stage: Stage | None
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :returns: The profiles of the window with their books, and the number of all the profiles that match.
        :rtype: Slice[ListedProfile]
        """
        window = await self.profiles(actor, stage, request)
        books = await self._uow.recipe_profiles.count_books([profile.id for profile in window.items])
        return Slice(
            items=[ListedProfile(profile=profile, books=books.get(profile.id, 0)) for profile in window.items],
            total=window.total,
        )

    async def get(self, actor: Actor, profile_id: RecipeProfileId) -> RecipeProfile:
        """Return a profile of the account, which is what its file is written from.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param profile_id: Identifier of the profile.
        :type profile_id: RecipeProfileId
        :returns: The profile.
        :rtype: RecipeProfile
        :raises NotFoundError: If the account has no such profile.
        """
        return await self._owned(actor, profile_id)

    async def duplicate(self, actor: Actor, profile_id: RecipeProfileId) -> RecipeProfile:
        """Save a copy of a profile under the name of the profile with ``(copy)`` after it, which is not the default.

        The steps are copied as they were checked when the profile was saved. No book is linked to the copy.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param profile_id: Identifier of the profile to copy.
        :type profile_id: RecipeProfileId
        :returns: The copy as stored.
        :rtype: RecipeProfile
        :raises NotFoundError: If the account has no such profile.
        """
        async with self._uow.change():
            profile = await self._owned(actor, profile_id)
            moment = self._clock.now()
            return await self._uow.recipe_profiles.add(
                evolve(
                    profile,
                    id=RecipeProfileId(uuid4()),
                    name=COPY_NAME.format(name=profile.name),
                    is_default=False,
                    created_at=moment,
                    updated_at=moment,
                )
            )

    async def import_profile(self, actor: Actor, stage: Stage, draft: ProfileDraft) -> RecipeProfile:
        """Save the profile a file holds, which gets new identifiers for its steps and is not the default.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param stage: Stage whose recipes the profile can be applied to.
        :type stage: Stage
        :param draft: Name, steps and order read from the file, which are checked as ``save`` checks them.
        :type draft: ProfileDraft
        :returns: The profile as stored.
        :rtype: RecipeProfile
        :raises InvalidParametersError: If the file needs a processor that is not installed, a step does not fit its
                                        processor, or stands off a required place in the usual order.
        """
        if missing := self._recipes.installed(draft.steps)[1]:
            raise InvalidParametersError(MISSING_IN_FILE.format(keys=', '.join(missing)))
        return await self.save(actor, stage, draft)

    async def save(self, actor: Actor, stage: Stage, draft: ProfileDraft) -> RecipeProfile:
        """Save the steps of a recipe as a profile of the account.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param stage: Stage whose recipes the profile can be applied to.
        :type stage: Stage
        :param draft: Name and steps of the profile, which are checked against their processors and their order.
        :type draft: ProfileDraft
        :returns: The profile as stored, which is not the default.
        :rtype: RecipeProfile
        :raises InvalidParametersError: If a step does not fit its processor, or stands off a required place in the
                                        usual order.
        """
        steps = await self._recipes.check(stage, draft.steps, order=draft.order)
        moment = self._clock.now()
        async with self._uow.change():
            return await self._uow.recipe_profiles.add(
                RecipeProfile(
                    id=RecipeProfileId(uuid4()),
                    account_id=actor.account_id,
                    stage=stage,
                    name=draft.name,
                    steps=steps,
                    order=draft.order,
                    created_at=moment,
                    updated_at=moment,
                )
            )

    async def replace(self, actor: Actor, profile_id: RecipeProfileId, draft: ProfileDraft) -> RecipeProfile:
        """Replace the name, the steps and the order of a profile, which is how a book saves its changes to its profile.

        The recipes made from the profile are not changed. They differ from it from now on, until each is saved to
        match it or reverted to it.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param profile_id: Identifier of the profile.
        :type profile_id: RecipeProfileId
        :param draft: New name, steps and order, which are checked against the processors of the profile's stage and
                      their order.
        :type draft: ProfileDraft
        :returns: The profile as stored, with the same stage and the same default mark.
        :rtype: RecipeProfile
        :raises NotFoundError: If the account has no such profile.
        :raises InvalidParametersError: If a step does not fit its processor, or stands off a required place in the
                                        usual order.
        """
        async with self._uow.change():
            profile = await self._owned(actor, profile_id)
            return await self._uow.recipe_profiles.update(
                evolve(
                    profile,
                    name=draft.name,
                    steps=await self._recipes.check(profile.stage, draft.steps, order=draft.order),
                    order=draft.order,
                    updated_at=self._clock.now(),
                )
            )

    async def link(
        self, actor: Actor, project_id: ProjectId, key: RecipeKey, profile_id: RecipeProfileId | None
    ) -> Recipe:
        """Record which profile a recipe of a book was made from, or that it was made from none.

        The steps of the recipe do not change and no page goes stale. A book that saves its steps as a new profile uses
        this to make the profile the one it is compared with.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param key: The stage and the identifier of the recipe, which must belong to that stage.
        :type key: RecipeKey
        :param profile_id: Identifier of the profile, or None to unlink the recipe.
        :type profile_id: RecipeProfileId | None
        :returns: The recipe as stored.
        :rtype: Recipe
        :raises NotFoundError: If the actor has no such project, the project has no such recipe of the stage, or the
                               account has no such profile.
        :raises InvalidParametersError: If the profile is for another stage than the recipe.
        """
        async with self._uow.change_book(project_id) as project:
            if not project.is_owned_by(actor):
                raise NotFoundError(project_id)
            recipe = await self._recipes.get(project_id, key.recipe_id, stage=key.stage)
            if profile_id is not None:
                profile = await self._owned(actor, profile_id)
                if profile.stage is not recipe.stage:
                    raise InvalidParametersError(
                        OTHER_STAGE.format(name=profile.name, actual=profile.stage.label, expected=recipe.stage.label)
                    )
            return await self._uow.recipes.update(evolve(recipe, profile_id=profile_id))

    async def rename(self, actor: Actor, profile_id: RecipeProfileId, name: str) -> RecipeProfile:
        """Give a profile another name.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param profile_id: Identifier of the profile.
        :type profile_id: RecipeProfileId
        :param name: New name.
        :type name: str
        :returns: The profile as stored.
        :rtype: RecipeProfile
        :raises NotFoundError: If the account has no such profile.
        """
        async with self._uow.change():
            profile = await self._owned(actor, profile_id)
            return await self._uow.recipe_profiles.update(evolve(profile, name=name, updated_at=self._clock.now()))

    async def set_default(self, actor: Actor, profile_id: RecipeProfileId, *, is_default: bool) -> RecipeProfile:
        """Choose a profile as the default of its stage, or stop it being one.

        The profile that was the default of the stage is demoted before the new one is promoted, so an account never
        has two defaults for a stage.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param profile_id: Identifier of the profile.
        :type profile_id: RecipeProfileId
        :param is_default: Whether new books start the stage with this profile.
        :type is_default: bool
        :returns: The profile as stored.
        :rtype: RecipeProfile
        :raises NotFoundError: If the account has no such profile.
        """
        async with self._uow.change():
            profile = await self._owned(actor, profile_id)
            if profile.is_default is is_default:
                return profile
            moment = self._clock.now()
            if is_default and (
                current := await self._uow.recipe_profiles.find_default(actor.account_id, profile.stage)
            ):
                await self._uow.recipe_profiles.update(evolve(current, is_default=False, updated_at=moment))
            return await self._uow.recipe_profiles.update(evolve(profile, is_default=is_default, updated_at=moment))

    async def remove(self, actor: Actor, profile_id: RecipeProfileId) -> None:
        """Delete a profile, which changes no recipe of any book.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param profile_id: Identifier of the profile.
        :type profile_id: RecipeProfileId
        :raises NotFoundError: If the account has no such profile.
        """
        async with self._uow.change():
            await self._owned(actor, profile_id)
            await self._uow.recipe_profiles.delete(profile_id)

    async def apply(
        self, actor: Actor, project_id: ProjectId, profile_id: RecipeProfileId, *, kind: RecipeKind
    ) -> AppliedProfile:
        """Put the steps of a profile into the recipe of one kind of page of the profile's stage, and link it.

        The pages the recipe processed become stale, as they do for any change of a recipe. The use case holds no block
        itself: it finds the recipe, which opens a block of its own when the stage has none, then stores the steps, and
        then links the recipe, each in a block of its own.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param profile_id: Identifier of the profile.
        :type profile_id: RecipeProfileId
        :param kind: The kind of page whose recipe takes the steps.
        :type kind: RecipeKind
        :returns: The recipe, which is linked to the profile, and the processors whose steps were left out.
        :rtype: AppliedProfile
        :raises NotFoundError: If the account has no such profile or project, or the stage has no recipe.
        :raises InvalidParametersError: If no step of the profile can run here, or a step no longer fits its processor.
        """
        await owned_project(self._uow.projects, actor, project_id)
        profile = await self._owned(actor, profile_id)
        steps, missing = self._recipes.installed(profile.steps)
        if missing and not any(step.enabled for step in steps):
            raise InvalidParametersError(NOTHING_TO_APPLY.format(keys=', '.join(missing)))
        recipe = await self._recipes.of_kind(project_id, profile.stage, kind)
        # The steps were checked when the profile was saved, and rules may have been added to the processors since
        draft = RecipeDraft(steps=steps, order=OrderMode.FREE, profile_id=profile.id)
        applied = await self._put(actor, project_id, RecipeKey(stage=profile.stage, recipe_id=recipe.id), draft)
        return AppliedProfile(recipe=applied, missing_processors=missing)

    async def reset(self, actor: Actor, project_id: ProjectId, key: RecipeKey) -> Recipe:
        """Put the steps a recipe starts with back into it, and mark the pages it processed stale.

        The steps are those of the account's default profile for the stage when the recipe is the one of text pages and
        the account has a usable profile, and otherwise those of the built-in template of the kind, which are the steps
        the stage would have had on its first opening. The recipe keeps its identifier and its kind, while every step is
        new, so the settings and edits the pages kept for the old steps no longer belong to any step. The recipe is
        linked to the default profile when its steps came from it, and to no profile when they came from a template.
        The use case holds no block itself, as ``apply`` holds none.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param key: The stage the request names and the identifier of the recipe, which must belong to that stage.
        :type key: RecipeKey
        :returns: The recipe as stored.
        :rtype: Recipe
        :raises NotFoundError: If the actor has no such project, the project has no such recipe of the stage, or the
                               stage has no steps by default.
        """
        await owned_project(self._uow.projects, actor, project_id)
        recipe = await self._recipes.get(project_id, key.recipe_id, stage=key.stage)
        draft = await self._recipes.default_draft(project_id, key.stage, recipe.kind)
        return await self._put(actor, project_id, key, draft)

    async def _put(self, actor: Actor, project_id: ProjectId, key: RecipeKey, draft: RecipeDraft) -> Recipe:
        """Put the steps of a draft into a recipe, and link the recipe to the profile the draft names, or to none.

        Called outside any block, since the two steps it takes each open one.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param key: The stage and the identifier of the recipe.
        :type key: RecipeKey
        :param draft: The steps, and the profile they come from.
        :type draft: RecipeDraft
        :returns: The recipe as stored.
        :rtype: Recipe
        :raises NotFoundError: If the actor has no such project, the project has no such recipe of the stage, or the
                               account has no such profile.
        :raises InvalidParametersError: If a step does not fit its processor, or stands off a required place in the
                                        usual order.
        """
        stored = await self._processing.save_recipe(actor, project_id, key, draft)
        if stored.profile_id == draft.profile_id:
            return stored
        return await self.link(actor, project_id, key, draft.profile_id)

    async def _owned(self, actor: Actor, profile_id: RecipeProfileId) -> RecipeProfile:
        """Return the actor's profile.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param profile_id: Identifier of the profile.
        :type profile_id: RecipeProfileId
        :returns: The profile, owned by the actor.
        :rtype: RecipeProfile
        :raises NotFoundError: If the profile does not exist or belongs to another account.
        """
        profile = await self._uow.recipe_profiles.get(profile_id)
        if profile.account_id != actor.account_id:
            raise NotFoundError(profile_id)
        return profile
