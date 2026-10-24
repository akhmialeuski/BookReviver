"""Tests for the modes of a run: what it keeps of the work of the pages, what it takes away, and how it asks first."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import ChangeSource, EditorKind, RunMode, Stage, StepLayer
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.geometry import Rotation
from bookreviver.domain.values import NewPageEdit, PageStageKey, StageRun
from bookreviver.services.run_plans import RunPlan
from tests.helpers.builders import open_for
from tests.helpers.page_batches import PageValues, run_impact
from tests.helpers.processing import open_book
from tests.helpers.processors import STRENGTH_PARAMETER, FakeProcessor
from tests.helpers.spreads import run_stage

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.entities import Actor, Page, PageHistoryEntry, PageStepState, PageVersion
    from bookreviver.domain.ids import BookId
    from bookreviver.domain.values import PageStepKey
    from tests.helpers.builders import BookOpener
    from tests.helpers.database import LocatedBookDatabases
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

FAKE_KEY: str = FakeProcessor.spec.key
# The name of the mode, as the test argument and as the key of the parameters of a run job
MODE_KEY: str = 'mode'
STRONGER: int = 2
ROTATION: NewPageEdit = NewPageEdit(kind=EditorKind.ROTATION, geometry=Rotation(degrees=1.5))
WORKED_PAGES: int = 2


async def worked_book(kit: ProcessingKit) -> tuple[Actor, BookId, list[Page], list[PageStepKey]]:
    """Seed a book of three pages whose first two have work: both a setting, and the first also an edit.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The actor, the book, the pages and the key of the geometry step on each of them.
    :rtype: tuple[Actor, BookRecord, list[Page], list[PageStepKey]]
    """
    actor, book_id = await kit.seed_book()
    pages = []
    for order_key in ('a0', 'a1', 'a2'):
        page, _ = await kit.seed_scan_page(book_id, order_key=order_key)
        await kit.seed_base_version(book_id, page)
        pages.append(page)
    keys = [await kit.edit_key(book_id, page, Stage.GEOMETRY, FAKE_KEY) for page in pages]
    for key in keys[:WORKED_PAGES]:
        await PageValues(kit, actor, book_id).set(key, STRENGTH_PARAMETER, STRONGER)
    await kit.edits().save(open_for(actor, book_id, databases=kit.databases, files=kit.files), keys[0], ROTATION, None)
    return actor, book_id, pages, keys


async def head_of(kit: ProcessingKit, book_id: BookId, page: Page) -> PageVersion:
    """Read the current version of the geometry stage of a page.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: Identifier of the book.
    :type book_id: BookId
    :param page: The page.
    :type page: Page
    :returns: The version the stage record names.
    :rtype: PageVersion
    """
    record = await kit.databases.open(book_id).read(
        lambda repos: repos.page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
    )
    assert record.head_version_id is not None
    return await kit.stored_version(book_id, record.head_version_id)


async def state_of(book_id: BookId, key: PageStepKey, *, databases: LocatedBookDatabases) -> PageStepState:
    """Read the state of a step on a page as committed.

    :param book_id: Book that holds the pages.
    :type book_id: BookId
    :param key: The page, the stage and the step.
    :type key: PageStepKey
    :param databases: Opens the databases of the books of the test.
    :type databases: LocatedBookDatabases
    :returns: The state.
    :rtype: PageStepState
    """
    return await databases.open(book_id).read(lambda repos: repos.page_step_states.get(key))


async def states_of(
    book_id: BookId, keys: Sequence[PageStepKey], *, databases: LocatedBookDatabases
) -> list[PageStepState | None]:
    """Read the states of some steps as committed, with None for a step that has neither a setting nor an edit.

    :param book_id: Book that holds the pages.
    :type book_id: BookId
    :param keys: The pages, the stages and the steps.
    :type keys: Sequence[PageStepKey]
    :param databases: Opens the databases of the books of the test.
    :type databases: LocatedBookDatabases
    :returns: The state found for each key, in order.
    :rtype: list[PageStepState | None]
    """
    return await databases.open(book_id).read(lambda repos: [repos.page_step_states.find(key) for key in keys])


async def histories(
    book_id: BookId, pages: Sequence[Page], *, databases: LocatedBookDatabases
) -> list[Sequence[PageHistoryEntry]]:
    """Read the history of the steps of each page as committed.

    :param book_id: Identifier of the book.
    :type book_id: BookId
    :param pages: The pages.
    :type pages: Sequence[Page]
    :param databases: Opens the databases of the books of the test.
    :type databases: LocatedBookDatabases
    :returns: The changes of each page, in order.
    :rtype: list[Sequence[PageHistoryEntry]]
    """
    return await databases.open(book_id).read(
        lambda repos: [repos.page_history.list_for_page(page.id) for page in pages]
    )


class TestRunImpact:
    """Tests for the count of the pages a mode of a run takes work from."""

    @pytest.mark.parametrize(
        (MODE_KEY, 'affected'),
        [(RunMode.KEEP, 0), (RunMode.SKIP_OWN, 0), (RunMode.DROP_OWN, WORKED_PAGES)],
        ids=['keep', 'skip-own', 'drop-own'],
    )
    async def test_each_mode_counts_the_pages_it_takes_work_from(
        self, fx_kit: ProcessingKit, mode: RunMode, affected: int, fx_open_book: BookOpener
    ) -> None:
        """Verify the two pages with an edit or a setting are counted, and left out of the run by the mode that skips them.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param mode: Mode under test.
        :type mode: RunMode
        :param affected: How many pages lose work to it.
        :type affected: int
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id, _, _ = await worked_book(fx_kit)
        impact = await run_impact(fx_kit).impact(
            fx_open_book(actor, book_id), StageRun(stage=Stage.GEOMETRY, mode=mode)
        )
        expect(impact.affected == affected)
        expect(
            (impact.pages, impact.own_pages) == (3 - (WORKED_PAGES if mode is RunMode.SKIP_OWN else 0), WORKED_PAGES)
        )
        assert_expectations()

    async def test_leaving_out_the_pages_with_work_of_their_own_goes_over_the_others(
        self, fx_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify the pages with an edit or a setting are counted, left out of the run and lose nothing, or lose both.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id, _, _ = await worked_book(fx_kit)
        counted = {
            mode: await run_impact(fx_kit).impact(
                fx_open_book(actor, book_id), StageRun(stage=Stage.GEOMETRY, mode=mode)
            )
            for mode in (RunMode.KEEP, RunMode.SKIP_OWN, RunMode.DROP_OWN)
        }
        own = counted[RunMode.KEEP].own_pages
        expect(own > 0)
        expect(counted[RunMode.SKIP_OWN].own_pages == own)
        expect((counted[RunMode.SKIP_OWN].pages, counted[RunMode.SKIP_OWN].affected) == (3 - own, 0))
        expect(counted[RunMode.DROP_OWN].affected == own)
        assert_expectations()

    async def test_only_the_pages_the_run_names_are_counted(
        self, fx_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify a run of one page counts that page's work and not the work of the others.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id, pages, _ = await worked_book(fx_kit)
        run = StageRun(stage=Stage.GEOMETRY, mode=RunMode.DROP_OWN, page_ids=(pages[1].id,))
        impact = await run_impact(fx_kit).impact(fx_open_book(actor, book_id), run)
        expect((impact.pages, impact.affected) == (1, 1))
        assert_expectations()


class TestKeep:
    """Tests for the usual run, which keeps the settings and the edits of the pages."""

    async def test_a_run_keeps_the_settings_and_the_edits_and_writes_no_change(self, fx_kit: ProcessingKit) -> None:
        """Verify a run with the default mode leaves every state as it was and adds nothing to the history.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_id, pages, keys = await worked_book(fx_kit)
        before = await histories(book_id, pages, databases=fx_kit.databases)
        await run_stage(fx_kit, actor, book_id, StageRun(stage=Stage.GEOMETRY))
        first = await state_of(book_id, keys[0], databases=fx_kit.databases)
        second = await state_of(book_id, keys[1], databases=fx_kit.databases)
        after = await histories(book_id, pages, databases=fx_kit.databases)
        expect(first.params == {STRENGTH_PARAMETER: STRONGER} and first.edit is not None)
        expect(second.params == {STRENGTH_PARAMETER: STRONGER})
        expect(after == before)
        assert_expectations()

    async def test_the_run_uses_the_settings_of_each_page(self, fx_kit: ProcessingKit) -> None:
        """Verify the version a run makes of a page with a setting holds the strength of the page, not of the recipe.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_id, pages, _ = await worked_book(fx_kit)
        await run_stage(fx_kit, actor, book_id, StageRun(stage=Stage.GEOMETRY))
        strengths = [(await head_of(fx_kit, book_id, page)).params[STRENGTH_PARAMETER] for page in pages]
        assert strengths == [STRONGER, STRONGER, 1]


class TestConfirmation:
    """Tests for the confirmation a mode that takes work away needs."""

    async def test_a_run_that_takes_work_is_refused_until_it_is_confirmed(
        self, fx_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify the request is a conflict that names the number of pages, and queues no job and changes nothing.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id, _, keys = await worked_book(fx_kit)
        run = StageRun(stage=Stage.GEOMETRY, mode=RunMode.DROP_OWN)
        with pytest.raises(ConflictError, match=r'takes the work of \d+ pages away'):
            await fx_kit.service().start_run(fx_open_book(actor, book_id), Stage.GEOMETRY, run)
        expect(fx_kit.board.put_jobs == [])
        expect(await states_of(book_id, keys[:1], databases=fx_open_book.databases) != [None])
        assert_expectations()

    async def test_a_mode_that_takes_nothing_needs_no_confirmation(
        self, fx_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify a book whose pages have no work is run in any mode, since the warning would count no page.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id = await fx_kit.seed_book()
        page, _ = await fx_kit.seed_scan_page(book_id)
        await fx_kit.seed_base_version(book_id, page)
        job = await fx_kit.service().start_run(
            fx_open_book(actor, book_id), Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY, mode=RunMode.DROP_OWN)
        )
        assert job.params[MODE_KEY] == RunMode.DROP_OWN.value


class TestDropOwn:
    """Tests for the mode that takes the settings and the edits of the pages away and runs them by the recipe."""

    async def test_the_settings_and_the_edits_go_and_the_pages_run_with_the_recipe(self, fx_kit: ProcessingKit) -> None:
        """Verify the run empties both pages, finds the shape again, and runs them with the strength of the recipe.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_id, pages, keys = await worked_book(fx_kit)
        run = StageRun(stage=Stage.GEOMETRY, mode=RunMode.DROP_OWN, confirm_overwrite=True)
        await run_stage(fx_kit, actor, book_id, run)
        states = await states_of(book_id, keys[:WORKED_PAGES], databases=fx_kit.databases)
        heads = [await head_of(fx_kit, book_id, page) for page in pages[:WORKED_PAGES]]
        expect(states == [None] * WORKED_PAGES)
        expect(all(head.edit_hash == '' for head in heads))
        expect(all(head.params[STRENGTH_PARAMETER] == 1 for head in heads))
        assert_expectations()

    async def test_the_pages_share_one_batch_that_one_undo_takes_back_everywhere(
        self, fx_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify the removals are one batch from the run, and one undo gives the settings and the edit back.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id, pages, keys = await worked_book(fx_kit)
        before = (await state_of(book_id, keys[0], databases=fx_open_book.databases)).edit
        run = StageRun(stage=Stage.GEOMETRY, mode=RunMode.DROP_OWN, confirm_overwrite=True)
        await run_stage(fx_kit, actor, book_id, run)
        removals = [
            change
            for key in keys[:WORKED_PAGES]
            for change in (await fx_kit.page_history().list(fx_open_book(actor, book_id), key)).changes
            if change.source is ChangeSource.RUN
        ]
        undone = await fx_kit.page_history().undo(fx_open_book(actor, book_id), keys[1], None)
        restored = [await state_of(book_id, key, databases=fx_open_book.databases) for key in keys[:WORKED_PAGES]]
        expect(len({removal.batch_id for removal in removals}) == 1 and removals[0].batch_id is not None)
        expect({removal.layer for removal in removals} == {StepLayer.HAND, StepLayer.SETTINGS})
        expect({undo.page_id for undo in undone} == {page.id for page in pages[:WORKED_PAGES]})
        expect([state.params for state in restored] == [{STRENGTH_PARAMETER: STRONGER}] * WORKED_PAGES)
        expect(before is not None and restored[0].edit is not None and restored[0].edit.edit_hash == before.edit_hash)
        assert_expectations()


class TestSkipOwn:
    """Tests for the mode that leaves the pages with work of their own out of the run."""

    async def test_only_the_pages_without_work_are_run_and_the_work_stays(self, fx_kit: ProcessingKit) -> None:
        """Verify the run goes over the third page alone, writes no change, and leaves the two worked pages as they were.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_id, pages, keys = await worked_book(fx_kit)
        run = StageRun(stage=Stage.GEOMETRY, mode=RunMode.SKIP_OWN)
        await run_stage(fx_kit, actor, book_id, run)
        first = await state_of(book_id, keys[0], databases=fx_kit.databases)
        records = await fx_kit.databases.open(book_id).read(
            lambda repos: [repos.page_stages.find(PageStageKey(page.id, Stage.GEOMETRY)) for page in pages],
        )
        expect(first.edit is not None and first.params == {STRENGTH_PARAMETER: STRONGER})
        expect(
            [record is not None and record.head_version_id is not None for record in records] == [False, False, True]
        )
        assert_expectations()


class TestApplyModeBlock:
    """Tests for the change that takes the work away, which reads the states of the steps inside it."""

    async def test_the_work_of_a_page_deleted_after_the_plan_was_made_is_not_taken_and_not_written_about(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a page that went away between the plan and the change has no state left, so no change is written.

        The plan chooses the pages and their recipes before the change, as a job does when it starts, and the states
        are read inside the change, so a page deleted in between is not counted, and the other pages lose their work.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_id, pages, keys = await worked_book(fx_kit)
        run = StageRun(stage=Stage.GEOMETRY, mode=RunMode.DROP_OWN)
        plan = RunPlan(recipes=fx_kit.parts().recipes, book=await open_book(fx_kit, book_id), run=run)
        await plan.recipes()
        await fx_kit.databases.open(book_id).change(lambda repos, _book_record: repos.pages.delete(pages[1].id))
        written = await plan.apply_mode()
        remaining = await fx_kit.databases.open(book_id).read(
            lambda repos: repos.page_step_states.list_for_step([pages[0].id], Stage.GEOMETRY, keys[0].step_id),
        )
        expect(len(written) > 0)
        expect({change.page_id for change in written} == {pages[0].id})
        expect(all(state.is_empty for state in remaining))
        assert_expectations()

    async def test_a_run_that_keeps_the_work_writes_no_change(self, fx_kit: ProcessingKit) -> None:
        """Verify the mode that keeps the work returns at once, with the states and the history as they were.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_id, _, keys = await worked_book(fx_kit)
        plan = RunPlan(
            recipes=fx_kit.parts().recipes,
            book=await open_book(fx_kit, book_id),
            run=StageRun(stage=Stage.GEOMETRY, mode=RunMode.KEEP),
        )
        written = await plan.apply_mode()
        state = await state_of(book_id, keys[0], databases=fx_kit.databases)
        expect(written == ())
        expect(state.edit is not None and state.params == {STRENGTH_PARAMETER: STRONGER})
        assert_expectations()
