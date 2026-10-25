"""The recipe profiles of a book: saving, copying and exchanging the steps of a recipe, applying them to its recipes.

A profile belongs to the book, like the recipes made from it, so nothing about a profile is read from the catalogue and
no account is asked. The recipe remembers the profile (``Recipe.profile_id``), so the book can tell how its steps
differ from the profile, but it is a recipe like any other: deleting the profile only clears the link in the recipes of
the book and changes no step. A profile reaches another book as a file only. The use cases check no owner: the access
dependency of the API opens the book, and a use case works on the ``Book`` it is given.

Applying a profile to a book puts its steps into the recipe of one kind of page of the stage and links the recipe to the
profile, through the use cases of ``ProcessingService``, which also mark the pages the recipe processed stale. A
processor that is not installed on the server is left out of the recipe and named in the result, so one profile serves
several machines.

A profile is exchanged as a file: the library exports one and imports another through the same checks that saving does.
An import refuses a file whose processors are not all installed, since a profile that cannot be applied whole would
be a profile the book holds without knowing it.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from attrs import evolve, frozen

from bookreviver.domain.entities import RecipeProfile
from bookreviver.domain.enums import OrderMode
from bookreviver.domain.errors import InvalidParametersError
from bookreviver.domain.ids import RecipeProfileId
from bookreviver.domain.values import RecipeDraft, RecipeKey, Slice

if TYPE_CHECKING:
    from bookreviver.domain.entities import BookRecord, Recipe
    from bookreviver.domain.enums import RecipeKind, Stage
    from bookreviver.domain.values import ProfileDraft, SliceRequest
    from bookreviver.ports.persistence import BookRepositories
    from bookreviver.services.book import Book
    from bookreviver.services.processing import ProcessingService
    from bookreviver.services.recipes import RecipeBook

NOTHING_TO_APPLY: str = 'No step of the profile can run here, since no processor is installed for {keys}.'
OTHER_STAGE: str = 'The profile {name} is for the {actual} stage, not for the {expected} stage.'
MISSING_IN_FILE: str = 'The profile file needs processors that are not installed here: {keys}.'
COPY_NAME: str = '{name} (copy)'
# Separates the keys of the missing processors in the text of an error
KEY_SEPARATOR: str = ', '


@frozen(kw_only=True)
class AppliedProfile:
    """The recipe a profile made in a book, and what was left out of it.

    :ivar recipe: The recipe of the book that took the steps of the profile.
    :ivar missing_processors: Keys of the processors of the profile that are not installed, whose steps were left out.
    """

    recipe: Recipe
    missing_processors: tuple[str, ...]


class RecipeProfiles:
    """Saves, copies, exchanges, renames, deletes and applies the recipe profiles of the book it is given."""

    def __init__(self, *, recipes: RecipeBook, processing: ProcessingService) -> None:
        """Work over the services of one request.

        :param recipes: The recipes of the book, which check the steps of a profile.
        :type recipes: RecipeBook
        :param processing: The use cases that put the steps of a profile into a recipe of a book.
        :type processing: ProcessingService
        """
        self._recipes = recipes
        self._processing = processing

    async def library(self, book: Book, stage: Stage | None, request: SliceRequest) -> Slice[RecipeProfile]:
        """Read one window of the profiles of a book, in the order of the stages, then oldest first.

        :param book: The open book.
        :type book: Book
        :param stage: The stage whose profiles are listed, or None for every stage.
        :type stage: Stage | None
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :returns: The profiles of the window, and the number of all the profiles that match.
        :rtype: Slice[RecipeProfile]
        """
        profiles = await book.read(lambda repos: repos.recipe_profiles.list_all(stage))
        return Slice(items=profiles[request.offset : request.offset + request.limit], total=len(profiles))

    async def get(self, book: Book, profile_id: RecipeProfileId) -> RecipeProfile:
        """Read a profile of a book.

        :param book: The open book.
        :type book: Book
        :param profile_id: Identifier of the profile.
        :type profile_id: RecipeProfileId
        :returns: The profile.
        :rtype: RecipeProfile
        :raises NotFoundError: If the book has no such profile.
        """
        return await book.read(lambda repos: repos.recipe_profiles.get(profile_id))

    async def duplicate(self, book: Book, profile_id: RecipeProfileId) -> RecipeProfile:
        """Save a copy of a profile under the name of the profile with ``(copy)`` after it.

        The steps are copied as they were checked when the profile was saved. No recipe is linked to the copy.

        :param book: The open book.
        :type book: Book
        :param profile_id: Identifier of the profile to copy.
        :type profile_id: RecipeProfileId
        :returns: The copy as stored.
        :rtype: RecipeProfile
        :raises NotFoundError: If the book has no such profile.
        """

        def work(repos: BookRepositories, _book_record: BookRecord) -> RecipeProfile:
            """Read the profile as it is now and store its copy.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it, which the work does not need.
            :type _book_record: BookRecord
            :returns: The copy as stored.
            :rtype: RecipeProfile
            :raises NotFoundError: If the book has no such profile.
            """
            current = repos.recipe_profiles.get(profile_id)
            moment = datetime.now(UTC)
            return repos.recipe_profiles.add(
                evolve(
                    current,
                    id=RecipeProfileId(uuid4()),
                    name=COPY_NAME.format(name=current.name),
                    created_at=moment,
                    updated_at=moment,
                )
            )

        return await book.change(work)

    async def import_profile(self, book: Book, stage: Stage, draft: ProfileDraft) -> RecipeProfile:
        """Save the profile a file holds, which gets new identifiers for its steps.

        :param book: The open book.
        :type book: Book
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
            raise InvalidParametersError(MISSING_IN_FILE.format(keys=KEY_SEPARATOR.join(missing)))
        return await self.save(book, stage, draft)

    async def save(self, book: Book, stage: Stage, draft: ProfileDraft) -> RecipeProfile:
        """Save the steps of a recipe as a new profile of a book.

        :param book: The open book.
        :type book: Book
        :param stage: Stage whose recipes the profile can be applied to.
        :type stage: Stage
        :param draft: Name and steps of the profile, which are checked against their processors and their order.
        :type draft: ProfileDraft
        :returns: The profile as stored.
        :rtype: RecipeProfile
        :raises InvalidParametersError: If a step does not fit its processor, or stands off a required place in the
                                        usual order.
        """
        steps = self._recipes.check(stage, draft.steps, order=draft.order)
        moment = datetime.now(UTC)
        profile = RecipeProfile(
            id=RecipeProfileId(uuid4()),
            stage=stage,
            name=draft.name,
            steps=steps,
            order=draft.order,
            created_at=moment,
            updated_at=moment,
        )
        return await book.change(lambda repos, _book_record: repos.recipe_profiles.add(profile))

    async def replace(self, book: Book, profile_id: RecipeProfileId, draft: ProfileDraft) -> RecipeProfile:
        """Replace the name, the steps and the order of a profile, which is how a book saves its changes to its profile.

        The recipes made from the profile are not changed. They differ from it from now on, until each is saved to
        match it or reverted to it.

        :param book: The open book.
        :type book: Book
        :param profile_id: Identifier of the profile.
        :type profile_id: RecipeProfileId
        :param draft: New name, steps and order, which are checked against the processors of the profile's stage and
                      their order.
        :type draft: ProfileDraft
        :returns: The profile as stored, with the same stage.
        :rtype: RecipeProfile
        :raises NotFoundError: If the book has no such profile.
        :raises InvalidParametersError: If a step does not fit its processor, or stands off a required place in the
                                        usual order.
        """

        def work(repos: BookRepositories, _book_record: BookRecord) -> RecipeProfile:
            """Read the profile as it is now, check the new steps against its stage and store the changed profile.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it, which the work does not need.
            :type _book_record: BookRecord
            :returns: The profile as stored.
            :rtype: RecipeProfile
            :raises NotFoundError: If the book has no such profile.
            :raises InvalidParametersError: If a step does not fit its processor, or stands off a required place in
                                            the usual order.
            """
            current = repos.recipe_profiles.get(profile_id)
            return repos.recipe_profiles.update(
                evolve(
                    current,
                    name=draft.name,
                    steps=self._recipes.check(current.stage, draft.steps, order=draft.order),
                    order=draft.order,
                    updated_at=datetime.now(UTC),
                )
            )

        return await book.change(work)

    async def link(self, book: Book, key: RecipeKey, profile_id: RecipeProfileId | None) -> Recipe:
        """Record which profile a recipe of a book was made from, or that it was made from none.

        The steps of the recipe do not change and no page goes stale. A book that saves its steps as a new profile uses
        this to make the profile the one it is compared with.

        :param book: The open book.
        :type book: Book
        :param key: The stage and the identifier of the recipe, which must belong to that stage.
        :type key: RecipeKey
        :param profile_id: Identifier of a profile of the book, or None to unlink the recipe.
        :type profile_id: RecipeProfileId | None
        :returns: The recipe as stored.
        :rtype: Recipe
        :raises NotFoundError: If the book has no such recipe of the stage, or no such profile.
        :raises InvalidParametersError: If the profile is for another stage than the recipe.
        """

        def work(repos: BookRepositories, _book_record: BookRecord) -> Recipe:
            """Check the recipe and the stage of the profile, and store the recipe with its new link.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it, which the work does not need.
            :type _book_record: BookRecord
            :returns: The recipe as stored.
            :rtype: Recipe
            :raises NotFoundError: If the book has no such recipe of the stage, or no such profile.
            :raises InvalidParametersError: If the profile is for another stage than the recipe.
            """
            recipe = self._recipes.get(repos, key.recipe_id, stage=key.stage)
            if profile_id is None:
                return repos.recipes.update(evolve(recipe, profile_id=None))
            profile = repos.recipe_profiles.get(profile_id)
            if profile.stage is not recipe.stage:
                raise InvalidParametersError(
                    OTHER_STAGE.format(name=profile.name, actual=profile.stage.label, expected=recipe.stage.label)
                )
            return repos.recipes.update(evolve(recipe, profile_id=profile.id))

        return await book.change(work)

    async def rename(self, book: Book, profile_id: RecipeProfileId, name: str) -> RecipeProfile:
        """Give a profile another name.

        :param book: The open book.
        :type book: Book
        :param profile_id: Identifier of the profile.
        :type profile_id: RecipeProfileId
        :param name: New name.
        :type name: str
        :returns: The profile as stored.
        :rtype: RecipeProfile
        :raises NotFoundError: If the book has no such profile.
        """

        def work(repos: BookRepositories, _book_record: BookRecord) -> RecipeProfile:
            """Read the profile as it is now and store it under the new name.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it, which the work does not need.
            :type _book_record: BookRecord
            :returns: The profile as stored.
            :rtype: RecipeProfile
            :raises NotFoundError: If the book has no such profile.
            """
            current = repos.recipe_profiles.get(profile_id)
            return repos.recipe_profiles.update(evolve(current, name=name, updated_at=datetime.now(UTC)))

        return await book.change(work)

    async def remove(self, book: Book, profile_id: RecipeProfileId) -> None:
        """Delete a profile, and clear the link to it in the recipes of the book, which changes no step.

        The link is cleared and the profile deleted in one change, so no recipe names a profile that is gone.

        :param book: The open book.
        :type book: Book
        :param profile_id: Identifier of the profile.
        :type profile_id: RecipeProfileId
        :raises NotFoundError: If the book has no such profile.
        """

        def work(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Clear the link of every recipe that was made from the profile, then delete the profile.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it, which the work does not need.
            :type _book_record: BookRecord
            :raises NotFoundError: If the book has no such profile.
            """
            repos.recipe_profiles.delete(profile_id)
            for recipe in repos.recipes.list_all():
                if recipe.profile_id == profile_id:
                    repos.recipes.update(evolve(recipe, profile_id=None))

        await book.change(work)

    async def apply(self, book: Book, profile_id: RecipeProfileId, *, kind: RecipeKind) -> AppliedProfile:
        """Put the steps of a profile into the recipe of one kind of page of the profile's stage, and link it.

        The pages the recipe processed become stale, as they do for any change of a recipe. The use case holds no
        transaction itself: it finds the recipe, which opens a change of its own when the stage has none, then stores
        the steps, and then links the recipe, each in a transaction of its own.

        :param book: The open book.
        :type book: Book
        :param profile_id: Identifier of the profile.
        :type profile_id: RecipeProfileId
        :param kind: The kind of page whose recipe takes the steps.
        :type kind: RecipeKind
        :returns: The recipe, which is linked to the profile, and the processors whose steps were left out.
        :rtype: AppliedProfile
        :raises NotFoundError: If the book has no such profile, or the stage has no recipe.
        :raises InvalidParametersError: If no step of the profile can run here, or a step no longer fits its processor.
        """
        profile = await self.get(book, profile_id)
        steps, missing = self._recipes.installed(profile.steps)
        if missing and not any(step.enabled for step in steps):
            raise InvalidParametersError(NOTHING_TO_APPLY.format(keys=KEY_SEPARATOR.join(missing)))
        recipe = await self._recipes.of_kind(book, profile.stage, kind)
        # The steps were checked when the profile was saved, and rules may have been added to the processors since
        draft = RecipeDraft(steps=steps, order=OrderMode.FREE, profile_id=profile.id)
        applied = await self._put(book, RecipeKey(stage=profile.stage, recipe_id=recipe.id), draft)
        return AppliedProfile(recipe=applied, missing_processors=missing)

    async def reset(self, book: Book, key: RecipeKey) -> Recipe:
        """Put the steps a recipe starts with back into it, and mark the pages it processed stale.

        The steps are those of the built-in template of the kind of the recipe, which are the steps the stage would have
        had on its first opening. The recipe keeps its identifier and its kind, while every step is new, so the
        settings and edits the pages kept for the old steps no longer belong to any step. The recipe is linked to no
        profile. The use case holds no transaction itself, as ``apply`` holds none.

        :param book: The open book.
        :type book: Book
        :param key: The stage the request names and the identifier of the recipe, which must belong to that stage.
        :type key: RecipeKey
        :returns: The recipe as stored.
        :rtype: Recipe
        :raises NotFoundError: If the book has no such recipe of the stage, or the stage has no steps by default.
        """
        recipe = await book.read(lambda repos: self._recipes.get(repos, key.recipe_id, stage=key.stage))
        return await self._put(book, key, self._recipes.template_draft(key.stage, recipe.kind))

    async def _put(self, book: Book, key: RecipeKey, draft: RecipeDraft) -> Recipe:
        """Put the steps of a draft into a recipe, and link the recipe to the profile the steps came from, or to none.

        Called outside the work of any transaction, since the two steps it takes each open one.

        :param book: The open book.
        :type book: Book
        :param key: The stage and the identifier of the recipe.
        :type key: RecipeKey
        :param draft: The steps, and the identifier of the profile they come from.
        :type draft: RecipeDraft
        :returns: The recipe as stored.
        :rtype: Recipe
        :raises NotFoundError: If the book has no such recipe of the stage.
        :raises InvalidParametersError: If a step does not fit its processor, or stands off a required place in the
                                        usual order.
        """
        stored = await self._processing.save_recipe(book, key, draft)
        if stored.profile_id == draft.profile_id:
            return stored
        return await self.link(book, key, draft.profile_id)
