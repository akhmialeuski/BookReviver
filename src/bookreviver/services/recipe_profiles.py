"""The recipe profiles of an account: saving the steps of a recipe, applying them to a book, and choosing the default.

A profile belongs to the account and no book refers to it, so a recipe made from a profile is a recipe like any other
and deleting the profile changes no book. Another account's profile is reported exactly like a missing one, as another
account's project is, so the identifiers of profiles cannot be probed.

Applying a profile adds a variant to the book and, when asked, makes it the active recipe, through the use cases of
``ProcessingService``, which also mark the pages the old active recipe processed stale. A processor that is not
installed on the server is left out of the variant and named in the result, so one profile serves several machines.
An account has at most one default profile for each stage, which the first opening of a stage in a new book reads
through ``RecipeBook``.
"""

from typing import TYPE_CHECKING
from uuid import uuid4

from attrs import evolve, frozen

from bookreviver.domain.entities import RecipeProfile
from bookreviver.domain.enums import OrderMode
from bookreviver.domain.errors import InvalidParametersError, NotFoundError
from bookreviver.domain.ids import RecipeProfileId
from bookreviver.domain.values import RecipeDraft, Slice

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor, Recipe
    from bookreviver.domain.enums import Stage
    from bookreviver.domain.ids import ProjectId
    from bookreviver.domain.values import SliceRequest
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.runtime import Clock
    from bookreviver.services.processing import ProcessingService
    from bookreviver.services.recipes import RecipeBook

NOTHING_TO_APPLY: str = 'No step of the profile can run here, since no processor is installed for {keys}.'


@frozen(kw_only=True)
class AppliedProfile:
    """The recipe a profile made in a book, and what was left out of it.

    :ivar recipe: The variant added to the book, which is active when the request asked for that.
    :ivar missing_processors: Keys of the processors of the profile that are not installed, whose steps were left out.
    """

    recipe: Recipe
    missing_processors: tuple[str, ...]


class RecipeProfiles:
    """Lists, saves, renames, deletes and applies the recipe profiles of the acting account."""

    def __init__(self, *, uow: UnitOfWork, recipes: RecipeBook, processing: ProcessingService, clock: Clock) -> None:
        """Work over the ports of one request.

        :param uow: Unit of work of the request, whose commit ends every changing use case.
        :type uow: UnitOfWork
        :param recipes: The recipes of the project, which check the steps of a profile.
        :type recipes: RecipeBook
        :param processing: The use cases that add a variant to a book and make it the active recipe.
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

    async def save(self, actor: Actor, stage: Stage, draft: RecipeDraft) -> RecipeProfile:
        """Save the steps of a recipe as a profile of the account.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param stage: Stage whose recipes the profile can be applied to.
        :type stage: Stage
        :param draft: Name and steps of the profile, which are checked against their processors and their order.
        :type draft: RecipeDraft
        :returns: The profile as stored, which is not the default.
        :rtype: RecipeProfile
        :raises InvalidParametersError: If a step does not fit its processor, or stands off a required place in the
                                        usual order.
        """
        moment = self._clock.now()
        profile = await self._uow.recipe_profiles.add(
            RecipeProfile(
                id=RecipeProfileId(uuid4()),
                account_id=actor.account_id,
                stage=stage,
                name=draft.name,
                steps=await self._recipes.check(stage, draft.steps, order=draft.order),
                created_at=moment,
                updated_at=moment,
            )
        )
        await self._uow.commit()
        return profile

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
        profile = await self._owned(actor, profile_id)
        renamed = await self._uow.recipe_profiles.update(evolve(profile, name=name, updated_at=self._clock.now()))
        await self._uow.commit()
        return renamed

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
        profile = await self._owned(actor, profile_id)
        if profile.is_default is is_default:
            return profile
        moment = self._clock.now()
        if is_default and (current := await self._uow.recipe_profiles.find_default(actor.account_id, profile.stage)):
            await self._uow.recipe_profiles.update(evolve(current, is_default=False, updated_at=moment))
        changed = await self._uow.recipe_profiles.update(evolve(profile, is_default=is_default, updated_at=moment))
        await self._uow.commit()
        return changed

    async def remove(self, actor: Actor, profile_id: RecipeProfileId) -> None:
        """Delete a profile, which changes no recipe of any book.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param profile_id: Identifier of the profile.
        :type profile_id: RecipeProfileId
        :raises NotFoundError: If the account has no such profile.
        """
        await self._owned(actor, profile_id)
        await self._uow.recipe_profiles.delete(profile_id)
        await self._uow.commit()

    async def apply(
        self, actor: Actor, project_id: ProjectId, profile_id: RecipeProfileId, *, activate: bool
    ) -> AppliedProfile:
        """Add the steps of a profile to a book as a variant of the profile's stage, and optionally make it active.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param profile_id: Identifier of the profile.
        :type profile_id: RecipeProfileId
        :param activate: Whether the variant becomes the active recipe of the stage.
        :type activate: bool
        :returns: The recipe, which is the variant, or the active recipe when ``activate`` is set, and the processors
                  whose steps were left out.
        :rtype: AppliedProfile
        :raises NotFoundError: If the account has no such profile or project, or the stage has no recipe.
        :raises InvalidParametersError: If no step of the profile can run here, or a step no longer fits its processor.
        """
        profile = await self._owned(actor, profile_id)
        steps, missing = self._recipes.installed(profile.steps)
        if missing and not any(step.enabled for step in steps):
            raise InvalidParametersError(NOTHING_TO_APPLY.format(keys=', '.join(missing)))
        # The steps were checked when the profile was saved, and rules may have been added to the processors since
        draft = RecipeDraft(name=profile.name, steps=steps, order=OrderMode.FREE)
        recipe = await self._processing.add_variant(actor, project_id, profile.stage, draft)
        if activate:
            recipe = await self._processing.activate(actor, project_id, profile.stage, recipe.id)
        return AppliedProfile(recipe=recipe, missing_processors=missing)

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
