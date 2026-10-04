"""Tests for the modes of a run: what it keeps of the work of the pages, what it takes away, and how it asks first."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.entities import Actor
from bookreviver.domain.enums import ChangeSource, EditorKind, RunMode, Stage, StepLayer
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.geometry import Rotation
from bookreviver.domain.values import NewPageEdit, PageStageKey, StageRun
from tests.helpers.builders import new_account_id
from tests.helpers.page_batches import run_impact
from tests.helpers.processors import STRENGTH_PARAMETER, FakeProcessor

if TYPE_CHECKING:
    from bookreviver.domain.entities import Page, PageVersion, Project
    from bookreviver.domain.values import PageStepKey
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

FAKE_KEY: str = FakeProcessor.spec.key
STRONGER: int = 2
ROTATION: NewPageEdit = NewPageEdit(kind=EditorKind.ROTATION, geometry=Rotation(degrees=1.5))
WORKED_PAGES: int = 2


async def worked_book(kit: ProcessingKit) -> tuple[Actor, Project, list[Page], list[PageStepKey]]:
    """Seed a book of three pages whose first two have work: both a setting, and the first also an edit.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The actor, the project, the pages and the key of the geometry step on each of them.
    :rtype: tuple[Actor, Project, list[Page], list[PageStepKey]]
    """
    actor, project = await kit.seed_project()
    pages = []
    for order_key in ('a0', 'a1', 'a2'):
        page, _ = await kit.seed_scan_page(project, order_key=order_key)
        await kit.seed_base_version(page)
        pages.append(page)
    keys = [await kit.edit_key(page, Stage.GEOMETRY, FAKE_KEY) for page in pages]
    for key in keys[:WORKED_PAGES]:
        await kit.page_settings().change(actor, project.id, key, STRENGTH_PARAMETER, STRONGER)
    await kit.edits().save(actor, project.id, keys[0], ROTATION, None)
    return actor, project, pages, keys


async def run_stage(kit: ProcessingKit, actor: Actor, project: Project, run: StageRun) -> None:
    """Start a run of a stage and let the worker do it.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: Account owning the project.
    :type actor: Actor
    :param project: Project to run in.
    :type project: Project
    :param run: The run.
    :type run: StageRun
    """
    job = await kit.service().start_run(actor, project.id, run.stage, run)
    await kit.jobs().run_stage(job.id)
    await kit.work_queue()


async def head_of(kit: ProcessingKit, page: Page) -> PageVersion:
    """Read the current version of the geometry stage of a page.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param page: The page.
    :type page: Page
    :returns: The version the stage record names.
    :rtype: PageVersion
    """
    record = await kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
    assert record.head_version_id is not None
    return await kit.stored_version(record.head_version_id)


class TestRunImpact:
    """Tests for the count of the pages a mode of a run takes work from."""

    @pytest.mark.parametrize(
        ('mode', 'affected'),
        [(RunMode.KEEP, 0), (RunMode.REPLACE_HAND, 1), (RunMode.RESET_SETTINGS, 2)],
        ids=['keep', 'replace-hand', 'reset-settings'],
    )
    async def test_each_mode_counts_the_pages_it_takes_work_from(
        self, fx_kit: ProcessingKit, mode: RunMode, affected: int
    ) -> None:
        """Verify the edit of one page and the settings of two are counted by the mode that takes them away.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param mode: Mode under test.
        :type mode: RunMode
        :param affected: How many pages lose work to it.
        :type affected: int
        """
        actor, project, _, _ = await worked_book(fx_kit)
        impact = await run_impact(fx_kit).impact(actor, project.id, StageRun(stage=Stage.GEOMETRY, mode=mode))
        expect(impact.affected == affected)
        expect((impact.pages, impact.hand_pages, impact.settings_pages) == (3, 1, WORKED_PAGES))
        assert_expectations()

    async def test_only_the_pages_the_run_names_are_counted(self, fx_kit: ProcessingKit) -> None:
        """Verify a run of one page counts that page's work and not the work of the others.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, _ = await worked_book(fx_kit)
        run = StageRun(stage=Stage.GEOMETRY, mode=RunMode.RESET_SETTINGS, page_ids=(pages[1].id,))
        impact = await run_impact(fx_kit).impact(actor, project.id, run)
        expect((impact.pages, impact.affected) == (1, 1))
        assert_expectations()

    async def test_a_stranger_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Verify the count of a project of another account is refused like a missing project.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project, _, _ = await worked_book(fx_kit)
        stranger = Actor(account_id=new_account_id())
        with pytest.raises(NotFoundError):
            await run_impact(fx_kit).impact(stranger, project.id, StageRun(stage=Stage.GEOMETRY))


class TestKeep:
    """Tests for the usual run, which keeps the settings and the edits of the pages."""

    async def test_a_run_keeps_the_settings_and_the_edits_and_writes_no_change(self, fx_kit: ProcessingKit) -> None:
        """Verify a run with the default mode leaves every state as it was and adds nothing to the history.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, keys = await worked_book(fx_kit)
        before = [await fx_kit.uow().page_step_changes.list_for_page(page.id) for page in pages]
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        first = await fx_kit.uow().page_step_states.get(keys[0])
        second = await fx_kit.uow().page_step_states.get(keys[1])
        after = [await fx_kit.uow().page_step_changes.list_for_page(page.id) for page in pages]
        expect(first.params == {STRENGTH_PARAMETER: STRONGER} and first.edit is not None)
        expect(second.params == {STRENGTH_PARAMETER: STRONGER})
        expect(after == before)
        assert_expectations()

    async def test_the_run_uses_the_settings_of_each_page(self, fx_kit: ProcessingKit) -> None:
        """Verify the version a run makes of a page with a setting holds the strength of the page, not of the recipe.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, _ = await worked_book(fx_kit)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        strengths = [(await head_of(fx_kit, page)).params[STRENGTH_PARAMETER] for page in pages]
        assert strengths == [STRONGER, STRONGER, 1]


class TestConfirmation:
    """Tests for the confirmation a mode that takes work away needs."""

    @pytest.mark.parametrize('mode', [RunMode.REPLACE_HAND, RunMode.RESET_SETTINGS], ids=['replace-hand', 'reset'])
    async def test_a_run_that_takes_work_is_refused_until_it_is_confirmed(
        self, fx_kit: ProcessingKit, mode: RunMode
    ) -> None:
        """Verify the request is a conflict that names the number of pages, and queues no job and changes nothing.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param mode: Mode under test.
        :type mode: RunMode
        """
        actor, project, _, keys = await worked_book(fx_kit)
        run = StageRun(stage=Stage.GEOMETRY, mode=mode)
        with pytest.raises(ConflictError, match=r'takes the work of \d+ pages away'):
            await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, run)
        expect(len(fx_kit.recording.enqueued) == 0)
        expect(await fx_kit.uow().page_step_states.find(keys[0]) is not None)
        assert_expectations()

    async def test_the_message_names_the_pages_of_the_mode(self, fx_kit: ProcessingKit) -> None:
        """Verify the refusal of the reset names the two pages that change a field, not the one that has an edit.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _, _ = await worked_book(fx_kit)
        run = StageRun(stage=Stage.GEOMETRY, mode=RunMode.RESET_SETTINGS)
        with pytest.raises(ConflictError, match=r'takes the work of 2 pages away'):
            await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, run)

    async def test_a_mode_that_takes_nothing_needs_no_confirmation(self, fx_kit: ProcessingKit) -> None:
        """Verify a book whose pages have no work is run in any mode, since the warning would count no page.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        page, _ = await fx_kit.seed_scan_page(project)
        await fx_kit.seed_base_version(page)
        job = await fx_kit.service().start_run(
            actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY, mode=RunMode.REPLACE_HAND)
        )
        assert job.params['mode'] == RunMode.REPLACE_HAND.value


class TestReplaceHand:
    """Tests for the mode that removes the manual edits and finds the shape again."""

    async def test_the_edits_go_and_the_settings_stay(self, fx_kit: ProcessingKit) -> None:
        """Verify the run removes the edit of the first page, keeps both settings, and finds the shape again.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, keys = await worked_book(fx_kit)
        run = StageRun(stage=Stage.GEOMETRY, mode=RunMode.REPLACE_HAND, confirm_overwrite=True)
        await run_stage(fx_kit, actor, project, run)
        first = await fx_kit.uow().page_step_states.get(keys[0])
        second = await fx_kit.uow().page_step_states.get(keys[1])
        head = await head_of(fx_kit, pages[0])
        expect(first.edit is None and first.params == {STRENGTH_PARAMETER: STRONGER})
        expect(second.params == {STRENGTH_PARAMETER: STRONGER})
        expect(head.edit_hash == '')
        assert_expectations()

    async def test_the_removal_is_a_batch_of_the_run_that_one_undo_takes_back(self, fx_kit: ProcessingKit) -> None:
        """Verify the change is written from the run in the manual layer, and an undo gives the edit back.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _, keys = await worked_book(fx_kit)
        before = (await fx_kit.uow().page_step_states.get(keys[0])).edit
        run = StageRun(stage=Stage.GEOMETRY, mode=RunMode.REPLACE_HAND, confirm_overwrite=True)
        await run_stage(fx_kit, actor, project, run)
        history = await fx_kit.page_history().list(actor, project.id, keys[0])
        removal = history.changes[-1]
        undone = await fx_kit.page_history().undo(actor, project.id, keys[0], None)
        after = (await fx_kit.uow().page_step_states.get(keys[0])).edit
        expect((removal.source, removal.layer, removal.after) == (ChangeSource.RUN, StepLayer.HAND, None))
        expect(removal.batch_id is not None and len(undone) == 1)
        expect(before is not None and after is not None and after.edit_hash == before.edit_hash)
        assert_expectations()


class TestResetSettings:
    """Tests for the mode that returns every page to the settings of the recipe."""

    async def test_the_settings_go_and_the_edit_stays(self, fx_kit: ProcessingKit) -> None:
        """Verify the run removes the setting of both pages, keeps the edit, and runs the pages with the recipe.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, keys = await worked_book(fx_kit)
        run = StageRun(stage=Stage.GEOMETRY, mode=RunMode.RESET_SETTINGS, confirm_overwrite=True)
        await run_stage(fx_kit, actor, project, run)
        first = await fx_kit.uow().page_step_states.get(keys[0])
        second = await fx_kit.uow().page_step_states.find(keys[1])
        head = await head_of(fx_kit, pages[1])
        expect(first.params == {} and first.edit is not None)
        expect(second is None)
        expect(head.params[STRENGTH_PARAMETER] == 1)
        assert_expectations()

    async def test_the_pages_share_one_batch_that_one_undo_takes_back_everywhere(self, fx_kit: ProcessingKit) -> None:
        """Verify the two removals are one batch from the run, and one undo gives both settings back.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, keys = await worked_book(fx_kit)
        run = StageRun(stage=Stage.GEOMETRY, mode=RunMode.RESET_SETTINGS, confirm_overwrite=True)
        await run_stage(fx_kit, actor, project, run)
        removals = [(await fx_kit.page_history().list(actor, project.id, key)).changes[-1] for key in keys[:2]]
        undone = await fx_kit.page_history().undo(actor, project.id, keys[1], None)
        restored = [(await fx_kit.uow().page_step_states.get(key)).params for key in keys[:2]]
        expect(len({removal.batch_id for removal in removals}) == 1 and removals[0].batch_id is not None)
        expect(all(removal.source is ChangeSource.RUN for removal in removals))
        expect(sorted(undo.page_id for undo in undone) == sorted(page.id for page in pages[:2]))
        expect(restored == [{STRENGTH_PARAMETER: STRONGER}] * 2)
        assert_expectations()
