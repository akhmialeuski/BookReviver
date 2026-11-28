"""Tests for the freshness of a stage worked out from its inputs, with no mark written by the change of an input."""

from typing import TYPE_CHECKING, Any, cast

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.jobs import StageRun
from bookreviver.domain.margins import NormalizeParam
from bookreviver.domain.recipes import RecipeDraft
from bookreviver.domain.stages import PageStageKey, Stage, StageState
from bookreviver.domain.step_values import ValueScope
from bookreviver.services.stage_freshness import StageFreshness
from tests.harness.builders.page_batches import PageValues
from tests.harness.builders.processing import open_book
from tests.harness.builders.spreads import head_of, run_stage
from tests.harness.fakes.processors import STRENGTH_PARAMETER
from tests.integration.services.test_margins_book import seed_book
from tests.integration.services.test_page_settings import on, ran_book, step_of, strength_field
from tests.integration.services.test_processing_versions import ran_geometry

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.ids import BookId
    from bookreviver.domain.pages import Page
    from bookreviver.ports.persistence import BookRepositories
    from tests.harness.builders.domain import BookOpener
    from tests.harness.builders.processing import ProcessingKit

pytestmark = pytest.mark.anyio

# A margin of the normalize step other than the one it starts with, in millimetres
WIDER_MARGIN: float = 30.0
# A strength of the fake step other than the one the recipe gives it
STRONGER: int = 4
# The pages of the small book and of the large book whose reads are counted
SMALL_BOOK: int = 3
LARGE_BOOK: int = 30


async def stale_pages(kit: ProcessingKit, book_id: BookId, pages: Sequence[Page]) -> list[bool]:
    """Work out, from the inputs alone, whether the geometry of each page is out of date.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: Identifier of the book.
    :type book_id: BookId
    :param pages: The pages, each with a record of the geometry.
    :type pages: Sequence[Page]
    :returns: Whether each page is out of date, in the order of the pages.
    :rtype: list[bool]
    """
    freshness = StageFreshness(processors=kit.processors)

    def work(repos: BookRepositories) -> list[bool]:
        """Judge the records of the pages.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :returns: Whether each page is out of date.
        :rtype: list[bool]
        """
        keys = [PageStageKey(page.id, Stage.GEOMETRY) for page in pages]
        stale = freshness.stale(repos, [repos.page_stages.get(key) for key in keys])
        return [key in stale for key in keys]

    return await (await open_book(kit, book_id)).read(work)


class CountingRepositories:
    """The repositories of a transaction, counting every call of a method of any of them, which is a query each."""

    def __init__(self, repos: BookRepositories) -> None:
        """Count the calls made through the repositories.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        """
        self._repos = repos
        self.calls = 0

    def __getattr__(self, name: str) -> Any:
        """Give a repository whose methods count their calls.

        :param name: Name of the repository.
        :type name: str
        :returns: The counting repository.
        :rtype: Any
        """
        return CountingRepository(self, getattr(self._repos, name))


class CountingRepository:
    """One repository whose method calls are counted by the repositories it belongs to."""

    def __init__(self, counter: CountingRepositories, repository: object) -> None:
        """Count the calls of a repository.

        :param counter: The repositories that count.
        :type counter: CountingRepositories
        :param repository: The repository.
        :type repository: object
        """
        self._counter = counter
        self._repository = repository

    def __getattr__(self, name: str) -> Any:
        """Give a method of the repository that counts its call.

        :param name: Name of the method.
        :type name: str
        :returns: The method.
        :rtype: Any
        """
        method = getattr(self._repository, name)

        def counted(*args: object, **kwargs: object) -> object:
            """Count the call, then make it.

            :param args: Positional arguments.
            :type args: object
            :param kwargs: Keyword arguments.
            :type kwargs: object
            :returns: What the method returns.
            :rtype: object
            """
            self._counter.calls += 1
            return method(*args, **kwargs)

        return counted


async def reads_for(kit: ProcessingKit, count: int, fx_open_book: BookOpener) -> int:
    """Run the geometry on a book of some pages with a value for the odd ones, and count the reads of its freshness.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param count: Number of pages of the book.
    :type count: int
    :param fx_open_book: Opens the books of the test with their databases.
    :type fx_open_book: BookOpener
    :returns: The calls of repository methods that judging every page makes.
    :rtype: int
    """
    actor, book_id, pages, step_id = await ran_book(kit, count=count)
    await kit.page_settings().change(
        fx_open_book(actor, book_id), strength_field(step_id, on(ValueScope.ODD)), STRONGER
    )
    freshness = StageFreshness(processors=kit.processors)

    def work(repos: BookRepositories) -> int:
        """Judge every page through repositories that count.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :returns: The calls made.
        :rtype: int
        """
        records = list(repos.page_stages.list_for_stage(Stage.GEOMETRY))
        counting = CountingRepositories(repos)
        freshness.stale(cast('BookRepositories', counting), records)
        return counting.calls

    assert len(pages) == count
    return await (await open_book(kit, book_id)).read(work)


class TestFreshness:
    """Tests for the freshness of the geometry of pages, which no change of an input marks."""

    async def test_the_version_a_run_made_is_the_one_the_inputs_give(self, fx_kit: ProcessingKit) -> None:
        """Verify the identifier worked out from the inputs is the one the run made, so the page reads fresh.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_id, page, version = await ran_geometry(fx_kit)
        expect(await stale_pages(fx_kit, book_id, [page]) == [False])
        expect((await head_of(fx_kit, book_id, page, Stage.GEOMETRY)).id == version.id)
        assert_expectations()

    async def test_a_setting_of_the_page_makes_it_stale_and_taking_it_back_fresh_again(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a page whose own setting changed reads stale, and fresh again once the setting is taken back.

        The stored mark stays stale after the setting is taken back, while the inputs give the version again.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_id, page, _ = await ran_geometry(fx_kit)
        key = await fx_kit.edit_key(book_id, page, Stage.GEOMETRY, (await step_of(fx_kit, book_id)).processor_key)
        values = PageValues(fx_kit, actor, book_id)
        await values.set(key, STRENGTH_PARAMETER, STRONGER)
        changed = await stale_pages(fx_kit, book_id, [page])
        await values.take_back(key, STRENGTH_PARAMETER)
        expect(changed == [True])
        expect(await stale_pages(fx_kit, book_id, [page]) == [False])
        assert_expectations()

    async def test_a_value_of_the_odd_pages_makes_only_the_odd_page_stale(
        self, fx_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify a value for the odd pages makes the first page stale and leaves the second as it was.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id, pages, step_id = await ran_book(fx_kit, count=2)
        await fx_kit.page_settings().change(
            fx_open_book(actor, book_id), strength_field(step_id, on(ValueScope.ODD)), STRONGER
        )
        assert await stale_pages(fx_kit, book_id, pages) == [True, False]

    async def test_a_saved_recipe_makes_the_page_stale_and_the_steps_saved_back_fresh_again(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify other parameters of the recipe make the page stale, and the parameters it ran with make it fresh.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_id, page, _ = await ran_geometry(fx_kit)
        step = await step_of(fx_kit, book_id)
        stronger = evolve(step, params={**step.params, STRENGTH_PARAMETER: STRONGER})
        await fx_kit.edit_recipe(actor, book_id, Stage.GEOMETRY, RecipeDraft(steps=[stronger]))
        changed = await stale_pages(fx_kit, book_id, [page])
        await fx_kit.edit_recipe(actor, book_id, Stage.GEOMETRY, RecipeDraft(steps=[step]))
        expect(changed == [True])
        expect(await stale_pages(fx_kit, book_id, [page]) == [False])
        assert_expectations()

    async def test_a_run_after_a_change_makes_the_page_fresh(self, fx_kit: ProcessingKit) -> None:
        """Verify the run that follows a change of the recipe makes the version the inputs give.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_id, page, _ = await ran_geometry(fx_kit)
        step = await step_of(fx_kit, book_id)
        stronger = evolve(step, params={**step.params, STRENGTH_PARAMETER: STRONGER})
        await fx_kit.edit_recipe(actor, book_id, Stage.GEOMETRY, RecipeDraft(steps=[stronger]))
        await run_stage(fx_kit, actor, book_id, StageRun(stage=Stage.GEOMETRY))
        assert await stale_pages(fx_kit, book_id, [page]) == [False]

    async def test_a_failed_record_stays_failed_whatever_its_inputs(self, fx_kit: ProcessingKit) -> None:
        """Verify a failed record is never judged, so it is shown failed whether or not its inputs changed.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_id, page, _ = await ran_geometry(fx_kit)
        freshness = StageFreshness(processors=fx_kit.processors)

        step = await step_of(fx_kit, book_id)
        stronger = evolve(step, params={**step.params, STRENGTH_PARAMETER: STRONGER})
        await fx_kit.edit_recipe(actor, book_id, Stage.GEOMETRY, RecipeDraft(steps=[stronger]))

        def work(repos: BookRepositories) -> bool:
            """Judge the record of the page as failed, after its recipe changed.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: Whether it was found stale.
            :rtype: bool
            """
            failed = evolve(repos.page_stages.get(PageStageKey(page.id, Stage.GEOMETRY)), state=StageState.FAILED)
            return failed.key in freshness.stale(repos, [failed])

        assert await (await open_book(fx_kit, book_id)).read(work) is False

    async def test_the_reads_of_many_pages_do_not_grow_with_the_number_of_pages(
        self, fx_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify judging every page of a book of 30 pages reads as often as judging a book of 3.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        assert await reads_for(fx_kit, SMALL_BOOK, fx_open_book) == await reads_for(fx_kit, LARGE_BOOK, fx_open_book)


class TestFreshnessOfAStepThatReadsTheBook:
    """Tests for the freshness of pages whose recipe has a step that leaves fields to the book."""

    async def test_the_fields_the_book_gave_are_taken_from_the_versions_and_a_margin_makes_every_page_stale(
        self, fx_cv_kit: ProcessingKit
    ) -> None:
        """Verify a run of the whole book reads fresh, and a margin of the recipe, which no page has, makes all stale.

        The page size is left to the book, which the run worked out from all the pages, so the inputs of one page do
        not give it: it is taken from the version the step made on the way to the current one.

        :param fx_cv_kit: What the processing services of the test share, with the processors of OpenCV.
        :type fx_cv_kit: ProcessingKit
        """
        actor, book_id, pages = await seed_book(fx_cv_kit)
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.GEOMETRY))
        ran = await stale_pages(fx_cv_kit, book_id, pages)
        recipe = await fx_cv_kit.recipe_of(actor, book_id, Stage.GEOMETRY)
        [normalize] = recipe.steps
        wider = evolve(normalize, params={**normalize.params, NormalizeParam.MARGIN_TOP: WIDER_MARGIN})
        await fx_cv_kit.edit_recipe(actor, book_id, Stage.GEOMETRY, RecipeDraft(steps=[wider]))
        expect(ran == [False] * len(pages))
        expect(await stale_pages(fx_cv_kit, book_id, pages) == [True] * len(pages))
        assert_expectations()
