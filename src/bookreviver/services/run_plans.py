"""The pages and recipes a run of a stage goes over, and what its mode takes away from them.

A run goes over the pages it names, or over every page of the book that has an image, and a page is run by the recipe
of its kind. Of the steps of that recipe a run goes over the ones that are on up to the step it stops at.

A run keeps the settings the pages changed for those steps and the manual edits the steps read, unless its mode says
otherwise. ``RunMode.DROP_OWN`` removes both, so the steps run with the values of the recipe and the automatic run finds
the shape again. ``RunMode.SKIP_OWN`` takes nothing away and leaves the pages that have either out of the run. The
request counts the pages that lose work and refuses the run until the user confirmed it, and the job takes the work away
before the first page is run. Every layer removed is a change of the history of its page, from a run, and all of them
are one batch, so one undo gives the work back on every page.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from bookreviver.domain.enums import ChangeSource, PageOrigin, RunMode, StageState, VersionState
from bookreviver.domain.values import RunImpact
from bookreviver.services.book_measure import leaves_to_the_book
from bookreviver.services.books import book_pages
from bookreviver.services.page_batches import PageBatch

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from bookreviver.domain.entities import BookRecord, Page, PageHistoryEntry, PageStage, PageStepState, Recipe
    from bookreviver.domain.enums import Stage
    from bookreviver.domain.ids import PageId, PageVersionId, StepId
    from bookreviver.domain.values import StageRun
    from bookreviver.ports.persistence import BookRepositories
    from bookreviver.services.book import Book
    from bookreviver.services.recipes import RecipeBook


class RunPlan:
    """The pages of one run, the recipe of each, and what the mode of the run does to the work of the pages."""

    def __init__(self, *, recipes: RecipeBook, book: Book, run: StageRun) -> None:
        """Plan one run over the ports of a request or a job.

        :param recipes: The recipes of the book, which supply the recipe of each page.
        :type recipes: RecipeBook
        :param book: The open book owning the pages, which the pages and the states are read from and the work of the
                     mode is written to.
        :type book: Book
        :param run: What the run was asked to do.
        :type run: StageRun
        """
        self._recipes = recipes
        self._book = book
        self._run = run
        self._named_pages: Sequence[Page] | None = None
        self._pages: Sequence[Page] | None = None
        self._by_page: dict[PageId, Recipe] | None = None

    async def pages(self) -> Sequence[Page]:
        """Choose the pages a run goes over: the ones it names, or every page of the book that needs the stage run.

        A run that leaves out the pages with work of their own goes over the others. A run that names no pages, which
        is a run of the whole book, also leaves out the pages whose result of the stage is fresh already, made by the
        recipe the run picks for the page and through the step the run stops at. That is what lets a new run after an
        interrupted one, or after a restart of the process, go over only the pages without a fresh result. A run that
        names its pages goes over all of them, and the versions already made answer the steps they cover. A run that
        takes the work of the pages away also goes over every page, since the result of a page that had work of its own
        is not the one the recipe gives.

        :returns: The pages in book order, without the placeholders, which have no image.
        :rtype: Sequence[Page]
        :raises NotFoundError: If a page the run names is not one of the book.
        """
        if self._pages is None:
            found = await self._named()
            if self._run.mode is RunMode.SKIP_OWN:
                own = self._own_ids(await self._states())
                found = [page for page in found if page.id not in own]
            if self._run.page_ids is None and self._run.mode is not RunMode.DROP_OWN:
                found = await self._without_fresh(found)
            self._pages = found
        return self._pages

    async def recipes(self) -> dict[PageId, Recipe]:
        """Choose the recipe of every page of the run.

        :returns: The recipe of each page, by page identifier.
        :rtype: dict[PageId, Recipe]
        :raises NotFoundError: If the stage has no recipe.
        """
        picked = await self._picked()
        return {page.id: picked[page.id] for page in await self.pages()}

    async def impact(self) -> RunImpact:
        """Count the pages that would lose work to the mode of the run.

        :returns: The pages the run goes over, and how many of the pages it names have an edit or change a field on the
                  steps the run goes over. A run that leaves out the pages with work of their own counts them before it
                  leaves them out.
        :rtype: RunImpact
        """
        states = await self._states()
        return RunImpact(
            mode=self._run.mode,
            pages=len(await self.pages()),
            own_pages=len(self._own_ids(states)),
        )

    async def apply_mode(self) -> Sequence[PageHistoryEntry]:
        """Take the work the mode names away from the steps of the run on its pages, as one batch, in one transaction.

        Called outside any transaction, and it opens a ``Book.change`` of its own. The recipes are chosen before it,
        since choosing them may store the defaults in a transaction of their own, and the states of the steps are read
        inside it, so a page that was deleted meanwhile has none left to remove and a state a user changed is removed
        as it stands. A run that keeps the work changes nothing. The stage of a page is not marked stale, since the run
        that follows makes it again.

        :returns: The changes written, one for each layer removed, which are none when no page had any.
        :rtype: Sequence[PageHistoryEntry]
        """
        if self._run.mode is not RunMode.DROP_OWN:
            return ()
        picked = await self._picked()

        def work(repos: BookRepositories, _book_record: BookRecord) -> Sequence[PageHistoryEntry]:
            """Remove the settings and the manual edits of the steps of the run, as one batch.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it, which the removal does not read.
            :type _book_record: BookRecord
            :returns: The changes written, one for each layer removed.
            :rtype: Sequence[PageHistoryEntry]
            """
            batch = PageBatch(repos=repos, source=ChangeSource.RUN, moment=datetime.now(UTC))
            for state in self._states_in(repos, picked):
                batch.clear_work(state)
            return batch.flush()

        return await self._book.change(work)

    async def _named(self) -> Sequence[Page]:
        """Choose the pages the run names, or every page of the book that has an image, before any is left out.

        :returns: The pages in book order, without the placeholders, which have no image.
        :rtype: Sequence[Page]
        :raises NotFoundError: If a page the run names is not one of the book.
        """
        if self._named_pages is None:

            def work(repos: BookRepositories) -> list[Page]:
                """Read the pages the run names from one snapshot of the book.

                :param repos: Repositories of the transaction.
                :type repos: BookRepositories
                :returns: The pages in book order.
                :rtype: list[Page]
                :raises NotFoundError: If a page the run names is not one of the book.
                """
                if self._run.page_ids is None:
                    return book_pages(repos.pages)
                return list(repos.pages.list_by_ids(self._run.page_ids))

            found = await self._book.read(work)
            self._named_pages = [page for page in found if page.origin is not PageOrigin.PLACEHOLDER]
        return self._named_pages

    async def _without_fresh(self, pages: Sequence[Page]) -> list[Page]:
        """Leave out the pages whose result of the stage is fresh, ready and made by their recipe from fresh inputs.

        A record that stopped short of the step the run goes through, or went through a step the run does not, is not
        what this run makes, so its page stays. So does a page whose head version is not ready, which a run makes
        again, and a page with a stale earlier stage, which the run brings up to date before it reads it. The flag of
        the record alone does not say either, since a change of an earlier stage leaves the later records as they are.
        A page whose recipe takes the page size or the line height from the book stays too: the boxes of the other pages
        decide its size, and a box changed on one of them leaves this record fresh. The run finds the versions it made
        before by their identifier, so a page whose size did not change costs it no step.

        :param pages: Pages the run would go over.
        :type pages: Sequence[Page]
        :returns: The pages that have no fresh result of the stage, or whose result depends on the book, in book order.
        :rtype: list[Page]
        :raises NotFoundError: If the stage has no recipe.
        """
        picked = await self._picked()
        stage = self._run.stage

        def read(repos: BookRepositories) -> tuple[list[PageStage], set[PageVersionId]]:
            """Read the records of the pages and which head versions of the stage are ready, from one snapshot.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: Every record of the pages, and the identifiers of the ready head versions of the stage.
            :rtype: tuple[list[PageStage], set[PageVersionId]]
            """
            records = list(repos.page_stages.list_for_pages([page.id for page in pages]))
            heads = [record.head_version_id for record in records if record.stage is stage and record.head_version_id]
            ready = {
                version.id for version in repos.page_versions.list_by_ids(heads) if version.state is VersionState.READY
            }
            return records, ready

        records, ready = await self._book.read(read)
        by_page: dict[PageId, dict[Stage, PageStage]] = {}
        for record in records:
            by_page.setdefault(record.page_id, {})[record.stage] = record

        def done(page: Page) -> bool:
            """Tell whether the page holds what the run would make, so the run leaves it out.

            :param page: A page the run would go over.
            :type page: Page
            :returns: Whether the result of the stage is fresh, ready, made from fresh earlier stages and independent of
                      the other pages of the book.
            :rtype: bool
            """
            stages = by_page.get(page.id, {})
            record = stages.get(stage)
            return (
                not leaves_to_the_book(picked[page.id], through_step=self._run.through_step)
                and self._is_fresh(record, picked[page.id], through_step=self._run.through_step)
                and record is not None
                and record.head_version_id in ready
                and all(earlier.state is StageState.FRESH for key, earlier in stages.items() if key in stage.earlier)
            )

        return [page for page in pages if not done(page)]

    @staticmethod
    def _is_fresh(record: PageStage | None, recipe: Recipe, *, through_step: int | None) -> bool:
        """Tell whether the record of a stage holds what a run of the recipe through the step would make.

        :param record: The record of the stage on the page, or None when the stage has not run on it.
        :type record: PageStage | None
        :param recipe: The recipe the run picks for the page.
        :type recipe: Recipe
        :param through_step: Index in the recipe of the last step the run goes through, or None for every step.
        :type through_step: int | None
        :returns: Whether the record is fresh, has a head version, and was made by the recipe through the same step.
        :rtype: bool
        """
        return (
            record is not None
            and record.state is StageState.FRESH
            and record.head_version_id is not None
            and record.recipe_id == recipe.id
            and record.through_step == recipe.stopped_at(through_step)
        )

    async def _picked(self) -> dict[PageId, Recipe]:
        """Choose the recipe of every page the run names, before any is left out.

        :returns: The recipe of each page, by page identifier.
        :rtype: dict[PageId, Recipe]
        :raises NotFoundError: If the stage has no recipe.
        """
        if self._by_page is None:
            self._by_page = await self._recipes.for_pages(self._book, self._run.stage, await self._named())
        return self._by_page

    @staticmethod
    def _own_ids(states: Sequence[PageStepState]) -> set[PageId]:
        """Name the pages that have work of their own in the states: an edit, or a field they changed.

        :param states: The states of the steps of the run.
        :type states: Sequence[PageStepState]
        :returns: The identifiers of the pages.
        :rtype: set[PageId]
        """
        return {state.page_id for state in states if state.edit is not None or state.params}

    async def _states(self) -> list[PageStepState]:
        """Read the states of the steps the run goes over, each step on the pages the run names with it.

        The recipes are chosen first, outside the transaction that reads, since choosing them may store the defaults.

        :returns: The states that are stored, which are none for a step and a page without settings and edit.
        :rtype: list[PageStepState]
        """
        picked = await self._picked()
        return await self._book.read(lambda repos: self._states_in(repos, picked))

    def _states_in(self, repos: BookRepositories, picked: Mapping[PageId, Recipe]) -> list[PageStepState]:
        """Read the states of the steps the run goes over in the transaction of the caller.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param picked: The recipe of every page the run names.
        :type picked: Mapping[PageId, Recipe]
        :returns: The states that are stored, which are none for a step and a page without settings and edit.
        :rtype: list[PageStepState]
        """
        by_step: dict[StepId, list[PageId]] = {}
        for page_id, recipe in picked.items():
            for _, step in recipe.indexed_steps_through(self._run.through_step):
                by_step.setdefault(step.step_id, []).append(page_id)
        states: list[PageStepState] = []
        for step_id, page_ids in by_step.items():
            states.extend(repos.page_step_states.list_for_step(page_ids, self._run.stage, step_id))
        return states


class RunImpactService:
    """Counts the pages a run would take work from, for the user to confirm before the run is sent."""

    def __init__(self, *, recipes: RecipeBook) -> None:
        """Count over the ports of one request.

        :param recipes: The recipes of the book.
        :type recipes: RecipeBook
        """
        self._recipes = recipes

    async def impact(self, book: Book, run: StageRun) -> RunImpact:
        """Count the pages a run would take work from, which is what a run asks the user to confirm.

        :param book: The open book.
        :type book: Book
        :param run: The run, with its mode.
        :type run: StageRun
        :returns: How many pages the run goes over, how many of them have settings and edits on its steps, and how many
                  lose work to its mode.
        :rtype: RunImpact
        :raises NotFoundError: If the book has no such page, or the stage has no recipe.
        """
        plan = RunPlan(recipes=self._recipes, book=book, run=run)
        return await plan.impact()
