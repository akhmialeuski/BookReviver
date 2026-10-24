"""The recipe profiles of an account: saving, copying and exchanging the steps of a recipe, applying them to a book.

A profile belongs to the account and lives in the catalogue, while a recipe made from it lives in a book, which knows
nothing of accounts. The recipe remembers the profile (``Recipe.profile_id``), so the book can tell how its steps differ
from the profile, but it is a recipe like any other: deleting the profile only clears the link in the recipes of the
account's books and changes no step. The number of books that use a profile is counted from the recipes of the books
of the account. The use cases check no owner: the access dependencies of the API open a profile of the signed-in
account and hand over the books of the account, and a use case works on what it is given.

Applying a profile to a book puts its steps into the recipe of one kind of page of the stage and links the recipe to the
profile, through the use cases of ``ProcessingService``, which also mark the pages the recipe processed stale. A
processor that is not installed on the server is left out of the recipe and named in the result, so one profile serves
several machines. An account has at most one default profile for each stage, whose steps the recipe of text pages
starts with at the first opening of the stage in a new book, through ``RecipeBook``.

A profile is exchanged as a file: the library exports one and imports another through the same checks that saving does.
An import refuses a file whose processors are not all installed, since a profile that cannot be applied whole would
be a profile the account holds without knowing it.
"""

from collections import Counter
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
    from collections.abc import Iterable

    from bookreviver.domain.entities import Actor, BookRecord, Recipe
    from bookreviver.domain.enums import RecipeKind, Stage
    from bookreviver.domain.values import ProfileDraft
    from bookreviver.ports.persistence import BookRepositories, CatalogueRepositories, UnitOfWork
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
    """Saves, copies, exchanges, renames, deletes and applies the recipe profiles it is given."""

    def __init__(self, *, uow: UnitOfWork, recipes: RecipeBook, processing: ProcessingService) -> None:
        """Work over the ports of one request.

        :param uow: Unit of work of the request. A use case that changes the profiles of the account runs in one
                    ``change``, and ``link`` runs in the ``Book.change`` of the book it changes.
        :type uow: UnitOfWork
        :param recipes: The recipes of the book, which check the steps of a profile.
        :type recipes: RecipeBook
        :param processing: The use cases that put the steps of a profile into a recipe of a book.
        :type processing: ProcessingService
        """
        self._uow = uow
        self._recipes = recipes
        self._processing = processing

    async def library(self, profiles: Slice[RecipeProfile], books: Iterable[Book]) -> Slice[ListedProfile]:
        """Give each profile of a window the number of books that use it.

        :param profiles: The profiles of the window, and the number of all the profiles it was cut from.
        :type profiles: Slice[RecipeProfile]
        :param books: The books whose recipes are counted, those of the account that owns the profiles.
        :type books: Iterable[Book]
        :returns: The profiles of the window with their books, and the same total.
        :rtype: Slice[ListedProfile]
        """
        counts = await self._books_by_profile(books)
        return Slice(
            items=[ListedProfile(profile=profile, books=counts[profile.id]) for profile in profiles.items],
            total=profiles.total,
        )

    async def duplicate(self, profile: RecipeProfile) -> RecipeProfile:
        """Save a copy of a profile under the name of the profile with ``(copy)`` after it, which is not the default.

        The steps are copied as they were checked when the profile was saved. No book is linked to the copy.

        :param profile: The profile to copy.
        :type profile: RecipeProfile
        :returns: The copy as stored.
        :rtype: RecipeProfile
        :raises NotFoundError: If the profile was deleted meanwhile.
        """

        def work(repos: CatalogueRepositories) -> RecipeProfile:
            """Read the profile as it is now and store its copy.

            :param repos: Repositories of the transaction.
            :type repos: CatalogueRepositories
            :returns: The copy as stored.
            :rtype: RecipeProfile
            :raises NotFoundError: If the profile was deleted meanwhile.
            """
            current = repos.recipe_profiles.get(profile.id)
            moment = datetime.now(UTC)
            return repos.recipe_profiles.add(
                evolve(
                    current,
                    id=RecipeProfileId(uuid4()),
                    name=COPY_NAME.format(name=current.name),
                    is_default=False,
                    created_at=moment,
                    updated_at=moment,
                )
            )

        return await self._uow.change(work)

    async def import_profile(self, owner: Actor, stage: Stage, draft: ProfileDraft) -> RecipeProfile:
        """Save the profile a file holds, which gets new identifiers for its steps and is not the default.

        :param owner: Account the new profile belongs to.
        :type owner: Actor
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
        return await self.save(owner, stage, draft)

    async def save(self, owner: Actor, stage: Stage, draft: ProfileDraft) -> RecipeProfile:
        """Save the steps of a recipe as a new profile of an account.

        :param owner: Account the new profile belongs to.
        :type owner: Actor
        :param stage: Stage whose recipes the profile can be applied to.
        :type stage: Stage
        :param draft: Name and steps of the profile, which are checked against their processors and their order.
        :type draft: ProfileDraft
        :returns: The profile as stored, which is not the default.
        :rtype: RecipeProfile
        :raises InvalidParametersError: If a step does not fit its processor, or stands off a required place in the
                                        usual order.
        """
        steps = self._recipes.check(stage, draft.steps, order=draft.order)
        moment = datetime.now(UTC)
        profile = RecipeProfile(
            id=RecipeProfileId(uuid4()),
            account_id=owner.account_id,
            stage=stage,
            name=draft.name,
            steps=steps,
            order=draft.order,
            created_at=moment,
            updated_at=moment,
        )
        return await self._uow.change(lambda repos: repos.recipe_profiles.add(profile))

    async def replace(self, profile: RecipeProfile, draft: ProfileDraft) -> RecipeProfile:
        """Replace the name, the steps and the order of a profile, which is how a book saves its changes to its profile.

        The recipes made from the profile are not changed. They differ from it from now on, until each is saved to
        match it or reverted to it.

        :param profile: The profile.
        :type profile: RecipeProfile
        :param draft: New name, steps and order, which are checked against the processors of the profile's stage and
                      their order.
        :type draft: ProfileDraft
        :returns: The profile as stored, with the same stage and the same default mark.
        :rtype: RecipeProfile
        :raises NotFoundError: If the profile was deleted meanwhile.
        :raises InvalidParametersError: If a step does not fit its processor, or stands off a required place in the
                                        usual order.
        """

        def work(repos: CatalogueRepositories) -> RecipeProfile:
            """Read the profile as it is now, check the new steps against its stage and store the changed profile.

            :param repos: Repositories of the transaction.
            :type repos: CatalogueRepositories
            :returns: The profile as stored.
            :rtype: RecipeProfile
            :raises NotFoundError: If the profile was deleted meanwhile.
            :raises InvalidParametersError: If a step does not fit its processor, or stands off a required place in
                                            the usual order.
            """
            current = repos.recipe_profiles.get(profile.id)
            return repos.recipe_profiles.update(
                evolve(
                    current,
                    name=draft.name,
                    steps=self._recipes.check(current.stage, draft.steps, order=draft.order),
                    order=draft.order,
                    updated_at=datetime.now(UTC),
                )
            )

        return await self._uow.change(work)

    async def link(self, book: Book, key: RecipeKey, profile: RecipeProfile | None) -> Recipe:
        """Record which profile a recipe of a book was made from, or that it was made from none.

        The steps of the recipe do not change and no page goes stale. A book that saves its steps as a new profile uses
        this to make the profile the one it is compared with.

        :param book: The open book.
        :type book: Book
        :param key: The stage and the identifier of the recipe, which must belong to that stage.
        :type key: RecipeKey
        :param profile: The profile, or None to unlink the recipe.
        :type profile: RecipeProfile | None
        :returns: The recipe as stored.
        :rtype: Recipe
        :raises NotFoundError: If the book has no such recipe of the stage.
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
            :raises NotFoundError: If the book has no such recipe of the stage.
            :raises InvalidParametersError: If the profile is for another stage than the recipe.
            """
            recipe = self._recipes.get(repos, key.recipe_id, stage=key.stage)
            if profile is None:
                return repos.recipes.update(evolve(recipe, profile_id=None))
            if profile.stage is not recipe.stage:
                raise InvalidParametersError(
                    OTHER_STAGE.format(name=profile.name, actual=profile.stage.label, expected=recipe.stage.label)
                )
            return repos.recipes.update(evolve(recipe, profile_id=profile.id))

        return await book.change(work)

    async def rename(self, profile: RecipeProfile, name: str) -> RecipeProfile:
        """Give a profile another name.

        :param profile: The profile.
        :type profile: RecipeProfile
        :param name: New name.
        :type name: str
        :returns: The profile as stored.
        :rtype: RecipeProfile
        :raises NotFoundError: If the profile was deleted meanwhile.
        """

        def work(repos: CatalogueRepositories) -> RecipeProfile:
            """Read the profile as it is now and store it under the new name.

            :param repos: Repositories of the transaction.
            :type repos: CatalogueRepositories
            :returns: The profile as stored.
            :rtype: RecipeProfile
            :raises NotFoundError: If the profile was deleted meanwhile.
            """
            current = repos.recipe_profiles.get(profile.id)
            return repos.recipe_profiles.update(evolve(current, name=name, updated_at=datetime.now(UTC)))

        return await self._uow.change(work)

    async def set_default(self, profile: RecipeProfile, *, is_default: bool) -> RecipeProfile:
        """Choose a profile as the default of its stage, or stop it being one.

        The profile that was the default of the stage for the same account is demoted before the new one is promoted,
        so an account never has two defaults for a stage.

        :param profile: The profile.
        :type profile: RecipeProfile
        :param is_default: Whether new books start the stage with this profile.
        :type is_default: bool
        :returns: The profile as stored.
        :rtype: RecipeProfile
        :raises NotFoundError: If the profile was deleted meanwhile.
        """

        def work(repos: CatalogueRepositories) -> RecipeProfile:
            """Demote the default of the stage when a new one is chosen, and store the profile with its mark.

            :param repos: Repositories of the transaction.
            :type repos: CatalogueRepositories
            :returns: The profile as stored.
            :rtype: RecipeProfile
            :raises NotFoundError: If the profile was deleted meanwhile.
            """
            current = repos.recipe_profiles.get(profile.id)
            if current.is_default is is_default:
                return current
            moment = datetime.now(UTC)
            if is_default and (default := repos.recipe_profiles.find_default(current.account_id, current.stage)):
                repos.recipe_profiles.update(evolve(default, is_default=False, updated_at=moment))
            return repos.recipe_profiles.update(evolve(current, is_default=is_default, updated_at=moment))

        return await self._uow.change(work)

    async def remove(self, profile: RecipeProfile, books: Iterable[Book]) -> None:
        """Delete a profile, and clear the link to it in the recipes of the books, which changes no step.

        The books are cleared one at a time, each in a ``Book.change`` of its own, and the profile goes last, so a
        failure keeps the profile and calling this again finishes the job.

        :param profile: The profile.
        :type profile: RecipeProfile
        :param books: The books whose recipes may link the profile, those of the account that owns it.
        :type books: Iterable[Book]
        """

        def unlink(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Clear the link of every recipe of the book that was made from the profile.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it, which the work does not need.
            :type _book_record: BookRecord
            """
            for recipe in repos.recipes.list_all():
                if recipe.profile_id == profile.id:
                    repos.recipes.update(evolve(recipe, profile_id=None))

        for book in books:
            await book.change(unlink)
        await self._uow.change(lambda repos: repos.recipe_profiles.delete(profile.id))

    async def apply(self, book: Book, profile: RecipeProfile, *, kind: RecipeKind) -> AppliedProfile:
        """Put the steps of a profile into the recipe of one kind of page of the profile's stage, and link it.

        The pages the recipe processed become stale, as they do for any change of a recipe. The use case holds no
        transaction itself: it finds the recipe, which opens a change of its own when the stage has none, then stores
        the steps, and then links the recipe, each in a transaction of its own.

        :param book: The open book.
        :type book: Book
        :param profile: The profile.
        :type profile: RecipeProfile
        :param kind: The kind of page whose recipe takes the steps.
        :type kind: RecipeKind
        :returns: The recipe, which is linked to the profile, and the processors whose steps were left out.
        :rtype: AppliedProfile
        :raises NotFoundError: If the stage has no recipe.
        :raises InvalidParametersError: If no step of the profile can run here, or a step no longer fits its processor.
        """
        steps, missing = self._recipes.installed(profile.steps)
        if missing and not any(step.enabled for step in steps):
            raise InvalidParametersError(NOTHING_TO_APPLY.format(keys=KEY_SEPARATOR.join(missing)))
        recipe = await self._recipes.of_kind(book, profile.stage, kind)
        # The steps were checked when the profile was saved, and rules may have been added to the processors since
        draft = RecipeDraft(steps=steps, order=OrderMode.FREE, profile_id=profile.id)
        applied = await self._put(book, RecipeKey(stage=profile.stage, recipe_id=recipe.id), draft, profile)
        return AppliedProfile(recipe=applied, missing_processors=missing)

    async def reset(self, book: Book, key: RecipeKey) -> Recipe:
        """Put the steps a recipe starts with back into it, and mark the pages it processed stale.

        The steps are those of the account's default profile for the stage when the recipe is the one of text pages and
        the account has a usable profile, and otherwise those of the built-in template of the kind, which are the steps
        the stage would have had on its first opening. The recipe keeps its identifier and its kind, while every step is
        new, so the settings and edits the pages kept for the old steps no longer belong to any step. The recipe is
        linked to the default profile when its steps came from it, and to no profile when they came from a template.
        The use case holds no transaction itself, as ``apply`` holds none.

        :param book: The open book.
        :type book: Book
        :param key: The stage the request names and the identifier of the recipe, which must belong to that stage.
        :type key: RecipeKey
        :returns: The recipe as stored.
        :rtype: Recipe
        :raises NotFoundError: If the book has no such recipe of the stage, or the stage has no steps by default.
        """
        recipe = await book.read(lambda repos: self._recipes.get(repos, key.recipe_id, stage=key.stage))
        default = await self._recipes.default_profile(book.id, key.stage)
        draft = self._recipes.default_draft(key.stage, recipe.kind, default)
        return await self._put(book, key, draft, default if draft.profile_id is not None else None)

    async def _put(self, book: Book, key: RecipeKey, draft: RecipeDraft, profile: RecipeProfile | None) -> Recipe:
        """Put the steps of a draft into a recipe, and link the recipe to the profile the steps came from, or to none.

        Called outside the work of any transaction, since the two steps it takes each open one.

        :param book: The open book.
        :type book: Book
        :param key: The stage and the identifier of the recipe.
        :type key: RecipeKey
        :param draft: The steps, and the identifier of the profile they come from.
        :type draft: RecipeDraft
        :param profile: The profile the draft names, or None when it names none.
        :type profile: RecipeProfile | None
        :returns: The recipe as stored.
        :rtype: Recipe
        :raises NotFoundError: If the book has no such recipe of the stage.
        :raises InvalidParametersError: If a step does not fit its processor, or stands off a required place in the
                                        usual order.
        """
        stored = await self._processing.save_recipe(book, key, draft)
        if stored.profile_id == draft.profile_id:
            return stored
        return await self.link(book, key, profile)

    async def _books_by_profile(self, books: Iterable[Book]) -> Counter[RecipeProfileId]:
        """Count the books that have a recipe made from each profile.

        :param books: The books whose recipes are counted.
        :type books: Iterable[Book]
        :returns: The number of books by profile, which is zero for a profile no recipe was made from.
        :rtype: Counter[RecipeProfileId]
        """

        def profiles_of(repos: BookRepositories) -> set[RecipeProfileId]:
            """Read the profiles the recipes of one book were made from.

            :param repos: Repositories of the transaction of the book.
            :type repos: BookRepositories
            :returns: The profiles, each once however many recipes name it.
            :rtype: set[RecipeProfileId]
            """
            recipes = repos.recipes.list_all()
            return {recipe.profile_id for recipe in recipes if recipe.profile_id is not None}

        counts: Counter[RecipeProfileId] = Counter()
        for book in books:
            counts.update(await book.read(profiles_of))
        return counts
