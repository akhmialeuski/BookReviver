"""Tests for the manual edits of a page, which a processor reads as an input beside its parameters."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import EditorKind, Rendition, Stage, StageState
from bookreviver.domain.errors import InvalidParametersError, NotFoundError
from bookreviver.domain.events import PageStageChanged
from bookreviver.domain.geometry import Line, Point, Rect, Rotation
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import NewPageEdit, PageEditKey, PageStageKey, StageRun
from tests.helpers.builders import make_page_stage
from tests.helpers.processors import CleanupProcessor, FakeProcessor
from tests.helpers.storage import upload

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor, Page, Project
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

FAKE_KEY: str = FakeProcessor.spec.key
ERASER_KEY: str = CleanupProcessor.spec.key
ROTATION: NewPageEdit = NewPageEdit(kind=EditorKind.ROTATION, geometry=Rotation(degrees=1.5))
MASK_CONTENT: bytes = b'mask-bytes' * 1000


async def prepared(kit: ProcessingKit) -> tuple[Actor, Project, Page]:
    """Seed a project with a scan page whose geometry stage has been run once, which an edit makes stale.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The actor, the project and the page.
    :rtype: tuple[Actor, Project, Page]
    """
    actor, project = await kit.seed_project()
    page, _ = await kit.seed_scan_page(project)
    await kit.seed_base_version(page)
    job = await kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
    await kit.jobs().run_stage(job.id)
    await kit.work_queue()
    return actor, project, page


class TestSave:
    """Tests for EditService.save."""

    async def test_edit_is_stored_with_its_hash_and_marks_the_stage_stale(self, fx_kit: ProcessingKit) -> None:
        """Verify a saved edit is listed with its hash, marks the stage stale, announces it and computes nothing.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared(fx_kit)
        runs = fx_kit.fake.runs
        stored = await fx_kit.edits().save(
            actor, project.id, PageEditKey(page.id, Stage.GEOMETRY, FAKE_KEY), ROTATION, None
        )
        listed = await fx_kit.edits().list(actor, project.id, page.id, Stage.GEOMETRY)
        record = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        expect(listed == [stored])
        expect(stored.edit_hash == stored.edit_hash.lower() and len(stored.edit_hash) == 16)
        expect(record.state is StageState.STALE)
        expect(fx_kit.fake.runs == runs)
        expect(
            any(
                isinstance(event, PageStageChanged) and event.stage.state is StageState.STALE
                for event in fx_kit.events.published
            )
        )
        assert_expectations()

    async def test_edit_changes_the_version_the_next_run_makes_and_going_back_finds_the_old_one(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the edit joins the identifier like a parameter: a new edit, a new version, and no edit, the old one.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared(fx_kit)
        key = PageEditKey(page.id, Stage.GEOMETRY, FAKE_KEY)

        async def rerun() -> str:
            """Run the geometry stage again and return the identifier of its current version.

            :returns: The identifier of the version the stage record names.
            :rtype: str
            """
            job = await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
            await fx_kit.jobs().run_stage(job.id)
            await fx_kit.work_queue()
            record = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
            assert record.head_version_id is not None
            return record.head_version_id

        before = await rerun()
        await fx_kit.edits().save(actor, project.id, key, ROTATION, None)
        edited = await rerun()
        await fx_kit.edits().delete(actor, project.id, key)
        back = await rerun()
        expect((before != edited, back == before) == (True, True))
        assert_expectations()

    async def test_saving_again_replaces_the_edit(self, fx_kit: ProcessingKit) -> None:
        """Verify a processor has one edit on a page, and the second save replaces the first.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared(fx_kit)
        key = PageEditKey(page.id, Stage.GEOMETRY, FAKE_KEY)
        await fx_kit.edits().save(actor, project.id, key, ROTATION, None)
        second = NewPageEdit(kind=EditorKind.ROTATION, geometry=Rotation(degrees=-2))
        replaced = await fx_kit.edits().save(actor, project.id, key, second, None)
        assert await fx_kit.edits().list(actor, project.id, page.id, Stage.GEOMETRY) == [replaced]

    @pytest.mark.parametrize(
        ('key_stage', 'processor', 'edit', 'match'),
        [
            (Stage.CLEANUP, FAKE_KEY, ROTATION, 'not to the Cleanup stage'),
            (
                Stage.GEOMETRY,
                FAKE_KEY,
                NewPageEdit(kind=EditorKind.RECT, geometry=Rect(left=0, top=0, width=1, height=1)),
                'Rotation editor',
            ),
            (Stage.GEOMETRY, FAKE_KEY, NewPageEdit(kind=EditorKind.ROTATION), 'needs its shape'),
            (Stage.CLEANUP, ERASER_KEY, NewPageEdit(kind=EditorKind.BRUSH_MASK), 'needs its mask'),
        ],
        ids=['other-stage', 'other-editor', 'no-shape', 'no-mask'],
    )
    async def test_edit_the_processor_does_not_read_is_rejected(
        self, fx_kit: ProcessingKit, key_stage: Stage, processor: str, edit: NewPageEdit, match: str
    ) -> None:
        """Reject an edit of another stage or editor, one without its shape, and a brush edit without its mask.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param key_stage: Stage the edit is saved for.
        :type key_stage: Stage
        :param processor: Key of the processor.
        :type processor: str
        :param edit: The edit.
        :type edit: NewPageEdit
        :param match: Text the error says.
        :type match: str
        """
        actor, project, page = await prepared(fx_kit)
        with pytest.raises(InvalidParametersError, match=match):
            await fx_kit.edits().save(actor, project.id, PageEditKey(page.id, key_stage, processor), edit, None)

    async def test_mask_is_stored_under_the_hash_of_its_edit(self, fx_kit: ProcessingKit) -> None:
        """Verify a mask is streamed in, kept under the key of its edit, and the scratch file is removed.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared(fx_kit)
        key = PageEditKey(page.id, Stage.CLEANUP, ERASER_KEY)
        edit = NewPageEdit(kind=EditorKind.BRUSH_MASK)
        stored = await fx_kit.edits().save(actor, project.id, key, edit, upload('mask.png', content=MASK_CONTENT))
        assert stored.mask_key is not None
        async with fx_kit.assets.readable(stored.mask_key) as mask:
            content = mask.read_bytes()
        directory = ProjectKeys(project.id).page_edit(page.id, ERASER_KEY, stored.edit_hash)
        expect(content == MASK_CONTENT)
        expect(stored.mask_key == f'{directory}/{Rendition.MASK}')
        async with fx_kit.assets.readable(ProjectKeys(project.id).page_edits(page.id, ERASER_KEY)) as folder:
            expect(sorted(entry.name for entry in folder.iterdir()) == [stored.edit_hash])
        assert_expectations()

    async def test_the_same_mask_saved_twice_keeps_one_copy(self, fx_kit: ProcessingKit) -> None:
        """Verify an equal edit finds the mask it stored before, which is never replaced.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared(fx_kit)
        key = PageEditKey(page.id, Stage.CLEANUP, ERASER_KEY)
        edit = NewPageEdit(kind=EditorKind.BRUSH_MASK)
        first = await fx_kit.edits().save(actor, project.id, key, edit, upload('mask.png', content=MASK_CONTENT))
        second = await fx_kit.edits().save(actor, project.id, key, edit, upload('mask.png', content=MASK_CONTENT))
        assert first.edit_hash == second.edit_hash

    async def test_page_of_another_project_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Reject an edit of a page the project does not have.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _ = await prepared(fx_kit)
        _, other_project = await fx_kit.seed_project()
        foreign, _ = await fx_kit.seed_scan_page(other_project)
        with pytest.raises(NotFoundError):
            await fx_kit.edits().save(
                actor, project.id, PageEditKey(foreign.id, Stage.GEOMETRY, FAKE_KEY), ROTATION, None
            )


class TestDelete:
    """Tests for EditService.delete."""

    async def test_deleted_edit_is_gone_and_the_stage_is_stale(self, fx_kit: ProcessingKit) -> None:
        """Verify deleting an edit removes it, marks the stage stale, and deleting it again is not found.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared(fx_kit)
        key = PageEditKey(page.id, Stage.GEOMETRY, FAKE_KEY)
        await fx_kit.edits().save(actor, project.id, key, ROTATION, None)
        uow = fx_kit.uow()
        await uow.page_stages.save(make_page_stage(page_id=page.id))
        await uow.commit()
        await fx_kit.edits().delete(actor, project.id, key)
        record = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        expect(await fx_kit.edits().list(actor, project.id, page.id, Stage.GEOMETRY) == [])
        expect(record.state is StageState.STALE)
        with pytest.raises(NotFoundError):
            await fx_kit.edits().delete(actor, project.id, key)
        assert_expectations()


def test_line_is_the_shape_of_the_split_editor() -> None:
    """Verify the split line the spread plugin reads is drawn by the line editor."""
    line = Line(start=Point(x=1, y=0), end=Point(x=2, y=10))
    assert NewPageEdit(kind=EditorKind.LINE, geometry=line).geometry == line
