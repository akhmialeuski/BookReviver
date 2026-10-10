"""Tests for the mark and the comment of a result and the log of their changes."""

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.domain.enums import ResultMark, Stage, VersionScale
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.ids import PageVersionId
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import RecipeDraft, ResultNote, StageRun, Step
from bookreviver.services.result_marks import ResultMarksService
from tests.helpers.builders import EPOCH
from tests.helpers.processors import STRENGTH_PARAMETER
from tests.helpers.spreads import run_stage
from tests.services.test_processing_versions import ran_geometry

if TYPE_CHECKING:
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

COMMENT: str = 'Too tight on the left.\nTry the other method.'
LATER: timedelta = timedelta(minutes=5)
PREVIEW_ID: PageVersionId = PageVersionId('abcdabcdabcdabcd')


def _marks(kit: ProcessingKit) -> ResultMarksService:
    """Build the result marks service over a new unit of work of the kit.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The service.
    :rtype: ResultMarksService
    """
    return ResultMarksService(uow=InMemoryUnitOfWork(kit.database), clock=kit.clock)


class TestSet:
    """Tests for ResultMarksService.set."""

    async def test_mark_and_comment_are_stored_and_the_identifier_stays(self, fx_kit: ProcessingKit) -> None:
        """Verify a mark and a multi-line comment land on the version, whose identifier and files do not change.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, version = await ran_geometry(fx_kit)
        answered = await _marks(fx_kit).set(
            actor, project.id, page.id, version.id, ResultNote(mark=ResultMark.BAD, comment=COMMENT)
        )
        stored = await fx_kit.stored_version(version.id)
        expect((answered.mark, answered.comment) == (ResultMark.BAD, COMMENT))
        expect((stored.mark, stored.comment) == (ResultMark.BAD, COMMENT))
        expect(stored == evolve(version, mark=ResultMark.BAD, comment=COMMENT))
        assert_expectations()

    async def test_mark_and_comment_can_be_changed_and_taken_off_and_every_change_is_logged(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify three changes leave three log entries with the values before and after, in order.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, version = await ran_geometry(fx_kit)
        await _marks(fx_kit).set(actor, project.id, page.id, version.id, ResultNote(mark=ResultMark.GOOD, comment=''))
        fx_kit.clock.moment = EPOCH + LATER
        await _marks(fx_kit).set(
            actor, project.id, page.id, version.id, ResultNote(mark=ResultMark.BAD, comment=COMMENT)
        )
        await _marks(fx_kit).set(actor, project.id, page.id, version.id, ResultNote(mark=None, comment=COMMENT))
        log = await _marks(fx_kit).changes(actor, project.id, page.id, version.id)
        assert [
            (c.sequence, c.mark_before, c.mark_after, c.comment_before, c.comment_after, c.created_at) for c in log
        ] == [
            (1, None, ResultMark.GOOD, '', '', EPOCH),
            (2, ResultMark.GOOD, ResultMark.BAD, '', COMMENT, EPOCH + LATER),
            (3, ResultMark.BAD, None, COMMENT, COMMENT, EPOCH + LATER),
        ]

    async def test_setting_what_is_stored_writes_nothing(self, fx_kit: ProcessingKit) -> None:
        """Verify a request equal to the stored mark and comment adds no entry to the log.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, version = await ran_geometry(fx_kit)
        await _marks(fx_kit).set(
            actor, project.id, page.id, version.id, ResultNote(mark=ResultMark.GOOD, comment=COMMENT)
        )
        await _marks(fx_kit).set(
            actor, project.id, page.id, version.id, ResultNote(mark=ResultMark.GOOD, comment=COMMENT)
        )
        untouched = await _marks(fx_kit).set(actor, project.id, page.id, version.id, ResultNote(mark=None, comment=''))
        await _marks(fx_kit).set(actor, project.id, page.id, version.id, ResultNote(mark=None, comment=''))
        log = await _marks(fx_kit).changes(actor, project.id, page.id, version.id)
        assert (len(log), untouched.mark) == (2, None)

    async def test_a_preview_takes_no_mark(self, fx_kit: ProcessingKit) -> None:
        """Verify a preview, which a collection deletes, is refused with a conflict.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, version = await ran_geometry(fx_kit)
        preview = evolve(version, id=PREVIEW_ID, scale=VersionScale.PREVIEW)
        uow = fx_kit.uow()
        async with uow.change_book(project.id):
            await uow.page_versions.add(preview)
        with pytest.raises(ConflictError):
            await _marks(fx_kit).set(
                actor, project.id, page.id, preview.id, ResultNote(mark=ResultMark.GOOD, comment='')
            )

    async def test_a_version_of_another_page_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Verify the version must belong to the page in the address.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, version = await ran_geometry(fx_kit)
        other, _ = await fx_kit.seed_scan_page(project, order_key='a1')
        assert other.id != page.id
        with pytest.raises(NotFoundError):
            await _marks(fx_kit).set(
                actor, project.id, other.id, version.id, ResultNote(mark=ResultMark.GOOD, comment='')
            )

    async def test_a_project_of_another_account_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Verify another account can neither mark nor read the log of a result of the book.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project, page, version = await ran_geometry(fx_kit)
        stranger, _ = await fx_kit.seed_project()
        with pytest.raises(NotFoundError):
            await _marks(fx_kit).set(
                stranger, project.id, page.id, version.id, ResultNote(mark=ResultMark.GOOD, comment='')
            )
        with pytest.raises(NotFoundError):
            await _marks(fx_kit).changes(stranger, project.id, page.id, version.id)

    async def test_a_comment_keeps_the_version_and_its_notes_through_a_collection(self, fx_kit: ProcessingKit) -> None:
        """Verify a collection that follows a run deletes neither the version with a comment nor its mark and files.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, first = await ran_geometry(fx_kit)
        await _marks(fx_kit).set(actor, project.id, page.id, first.id, ResultNote(mark=ResultMark.BAD, comment=COMMENT))
        stronger = RecipeDraft(steps=[Step(processor_key=fx_kit.fake.spec.key, params={STRENGTH_PARAMETER: 2})])
        await fx_kit.edit_recipe(actor, project, Stage.GEOMETRY, stronger)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        kept = await fx_kit.stored_version(first.id)
        async with fx_kit.assets.readable(ProjectKeys(project.id).version_directory(first)):
            pass
        assert (kept.mark, kept.comment) == (ResultMark.BAD, COMMENT)
