"""The recipe profiles of an account: saving, copying and exchanging the steps of a recipe, applying them to a book.

A profile belongs to the account. A recipe made from a profile remembers it (``Recipe.profile_id``), so the book can
tell how its steps differ from the profile, but it is a recipe like any other: deleting the profile only clears the
link and changes no book. Another account's profile is reported exactly like a missing one, as another account's
project is, so the identifiers of profiles cannot be probed.

Applying a profile adds a variant to the book and, when asked, makes it the active recipe, through the use cases of
``ProcessingService``, which also mark the pages the old active recipe processed stale. Applying it to some pages also
pins the variant to them and runs the stage on them, as giving a variant to pages does. A processor that is not
installed on the server is left out of the variant and named in the result, so one profile serves several machines.
An account has at most one default profile for each stage, which the first opening of a stage in a new book reads
through ``RecipeBook``.

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
from bookreviver.domain.values import RecipeDraft, Slice, StageRun
from bookreviver.services.projects import owned_project

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor, Job, Recipe
    from bookreviver.domain.enums import Stage
    from bookreviver.domain.ids import PageId, ProjectId
    from bookreviver.domain.values import RecipeKey, SliceRequest
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

    :ivar recipe: The variant added to the book, which is active when the request asked for that.
    :ivar missing_processors: Keys of the processors of the profile that are not installed, whose steps were left out.
    :ivar job: The queued run of the stage on the pages the profile was applied to, or None when it was applied to the
               book only.
    """

    recipe: Recipe
    missing_processors: tuple[str, ...]
    job: Job | None = None


class RecipeProfiles:
    """Lists, saves, copies, exchanges, renames, deletes and applies the recipe profiles of the acting account."""

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
        profile = await self._owned(actor, profile_id)
        moment = self._clock.now()
        copy = await self._uow.recipe_profiles.add(
            evolve(
                profile,
                id=RecipeProfileId(uuid4()),
                name=COPY_NAME.format(name=profile.name),
                is_default=False,
                created_at=moment,
                updated_at=moment,
            )
        )
        await self._uow.commit()
        return copy

    async def import_profile(self, actor: Actor, stage: Stage, draft: RecipeDraft) -> RecipeProfile:
        """Save the profile a file holds, which gets new identifiers for its steps and is not the default.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param stage: Stage whose recipes the profile can be applied to.
        :type stage: Stage
        :param draft: Name, steps and order read from the file, which are checked as ``save`` checks them.
        :type draft: RecipeDraft
        :returns: The profile as stored.
        :rtype: RecipeProfile
        :raises InvalidParametersError: If the file needs a processor that is not installed, a step does not fit its
                                        processor, or stands off a required place in the usual order.
        """
        if missing := self._recipes.installed(draft.steps)[1]:
            raise InvalidParametersError(MISSING_IN_FILE.format(keys=', '.join(missing)))
        return await self.save(actor, stage, draft)

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
                order=draft.order,
                created_at=moment,
                updated_at=moment,
            )
        )
        await self._uow.commit()
        return profile

    async def replace(self, actor: Actor, profile_id: RecipeProfileId, draft: RecipeDraft) -> RecipeProfile:
        """Replace the name, the steps and the order of a profile, which is how a book saves its changes to its profile.

        The recipes made from the profile are not changed. They differ from it from now on, until each is saved to
        match it or reverted to it.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param profile_id: Identifier of the profile.
        :type profile_id: RecipeProfileId
        :param draft: New name, steps and order, which are checked against the processors of the profile's stage and
                      their order.
        :type draft: RecipeDraft
        :returns: The profile as stored, with the same stage and the same default mark.
        :rtype: RecipeProfile
        :raises NotFoundError: If the account has no such profile.
        :raises InvalidParametersError: If a step does not fit its processor, or stands off a required place in the
                                        usual order.
        """
        profile = await self._owned(actor, profile_id)
        replaced = await self._uow.recipe_profiles.update(
            evolve(
                profile,
                name=draft.name,
                steps=await self._recipes.check(profile.stage, draft.steps, order=draft.order),
                order=draft.order,
                updated_at=self._clock.now(),
            )
        )
        await self._uow.commit()
        return replaced

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
        await owned_project(self._uow.projects, actor, project_id)
        recipe = await self._recipes.get(project_id, key.recipe_id, stage=key.stage)
        if profile_id is not None:
            profile = await self._owned(actor, profile_id)
            if profile.stage is not recipe.stage:
                raise InvalidParametersError(
                    OTHER_STAGE.format(name=profile.name, actual=profile.stage.label, expected=recipe.stage.label)
                )
        linked = await self._uow.recipes.update(evolve(recipe, profile_id=profile_id))
        await self._uow.commit()
        return linked

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
        self,
        actor: Actor,
        project_id: ProjectId,
        profile_id: RecipeProfileId,
        *,
        activate: bool,
        pages: tuple[PageId, ...] | None = None,
    ) -> AppliedProfile:
        """Add the steps of a profile to a book as a variant of the profile's stage, and optionally make it active.

        With pages, the variant is also pinned to them and the stage is run on them, as the application of any variant
        to some pages is. The variant is stored before the run is queued, so a run that is refused because the book is
        busy leaves the variant in the book, and nothing pinned.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param profile_id: Identifier of the profile.
        :type profile_id: RecipeProfileId
        :param activate: Whether the variant becomes the active recipe of the stage.
        :type activate: bool
        :param pages: The pages to pin the variant to and to run the stage on, or None to apply it to the book only.
        :type pages: tuple[PageId, ...] | None
        :returns: The recipe, which is the variant linked to the profile, or the active recipe when ``activate`` is set,
                  the processors whose steps were left out, and the queued run when there were pages.
        :rtype: AppliedProfile
        :raises NotFoundError: If the account has no such profile or project, the stage has no recipe, or a page is not
                               in the project.
        :raises InvalidParametersError: If no step of the profile can run here, or a step no longer fits its processor.
        :raises ConflictError: If pages were given and a run, a preview, a tile cutting, a collection or a measure of
                               the project is queued or running.
        """
        profile = await self._owned(actor, profile_id)
        steps, missing = self._recipes.installed(profile.steps)
        if missing and not any(step.enabled for step in steps):
            raise InvalidParametersError(NOTHING_TO_APPLY.format(keys=', '.join(missing)))
        # The steps were checked when the profile was saved, and rules may have been added to the processors since
        draft = RecipeDraft(name=profile.name, steps=steps, order=OrderMode.FREE, profile_id=profile.id)
        recipe = await self._processing.add_variant(actor, project_id, profile.stage, draft)
        if activate:
            recipe = await self._processing.activate(actor, project_id, profile.stage, recipe.id)
        job = None
        if pages is not None:
            run = StageRun(stage=profile.stage, recipe_id=recipe.id, page_ids=pages, pin=True)
            job = await self._processing.start_run(actor, project_id, profile.stage, run)
        return AppliedProfile(recipe=recipe, missing_processors=missing, job=job)

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
