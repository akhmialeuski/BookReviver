"""Tests for the mark and the comment of a result."""

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import ResultMark, Stage, VersionScale
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.ids import PageVersionId
from bookreviver.domain.keys import BookKeys
from bookreviver.domain.values import RecipeDraft, ResultNote, StageRun, Step
from bookreviver.services.result_marks import ResultMarksService
from tests.helpers.builders import EPOCH
from tests.helpers.processors import STRENGTH_PARAMETER
from tests.helpers.spreads import run_stage
from tests.services.test_processing_versions import ran_geometry

if TYPE_CHECKING:
    from tests.helpers.builders import BookOpener
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

COMMENT: str = 'Too tight on the left.\nTry the other method.'
LATER: timedelta = timedelta(minutes=5)
PREVIEW_ID: PageVersionId = PageVersionId('abcdabcdabcdabcd')


def _marks() -> ResultMarksService:
    """Build the result marks service over a new unit of work of the kit.

    :returns: The service.
    :rtype: ResultMarksService
    """
    return ResultMarksService()


class TestSet:
    """Tests for ResultMarksService.set."""

    async def test_mark_and_comment_are_stored_and_the_identifier_stays(
        self, fx_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify a mark and a multi-line comment land on the version, whose identifier and files do not change.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_record, page, version = await ran_geometry(fx_kit)
        answered = await _marks().set(
            fx_open_book(actor, book_record.id), page.id, version.id, ResultNote(mark=ResultMark.BAD, comment=COMMENT)
        )
        stored = await fx_kit.stored_version(book_record.id, version.id)
        expect((answered.mark, answered.comment) == (ResultMark.BAD, COMMENT))
        expect((stored.mark, stored.comment) == (ResultMark.BAD, COMMENT))
        expect(stored == evolve(version, mark=ResultMark.BAD, comment=COMMENT))
        assert_expectations()

    async def test_mark_and_comment_can_be_changed_and_taken_off(
        self, fx_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify the version holds the mark and the comment of the last of three changes, the mark taken off.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_record, page, version = await ran_geometry(fx_kit)
        await _marks().set(
            fx_open_book(actor, book_record.id), page.id, version.id, ResultNote(mark=ResultMark.GOOD, comment='')
        )
        fx_kit.clock.moment = EPOCH + LATER
        await _marks().set(
            fx_open_book(actor, book_record.id), page.id, version.id, ResultNote(mark=ResultMark.BAD, comment=COMMENT)
        )
        await _marks().set(
            fx_open_book(actor, book_record.id), page.id, version.id, ResultNote(mark=None, comment=COMMENT)
        )
        stored = await fx_kit.stored_version(book_record.id, version.id)
        assert (stored.mark, stored.comment) == (None, COMMENT)

    async def test_setting_what_is_stored_answers_the_stored_version(
        self, fx_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify a request equal to the stored mark and comment answers the version as it is stored.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_record, page, version = await ran_geometry(fx_kit)
        marked = await _marks().set(
            fx_open_book(actor, book_record.id), page.id, version.id, ResultNote(mark=ResultMark.GOOD, comment=COMMENT)
        )
        again = await _marks().set(
            fx_open_book(actor, book_record.id), page.id, version.id, ResultNote(mark=ResultMark.GOOD, comment=COMMENT)
        )
        assert again == marked == await fx_kit.stored_version(book_record.id, version.id)

    async def test_a_preview_takes_no_mark(self, fx_kit: ProcessingKit, fx_open_book: BookOpener) -> None:
        """Verify a preview, which a collection deletes, is refused with a conflict.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_record, page, version = await ran_geometry(fx_kit)
        preview = evolve(version, id=PREVIEW_ID, scale=VersionScale.PREVIEW)
        await fx_open_book.databases.open(book_record.id).change(
            lambda repos, _book_record: repos.page_versions.add(preview)
        )
        with pytest.raises(ConflictError):
            await _marks().set(
                fx_open_book(actor, book_record.id), page.id, preview.id, ResultNote(mark=ResultMark.GOOD, comment='')
            )

    async def test_a_version_of_another_page_is_not_found(
        self, fx_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify the version must belong to the page in the address.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_record, page, version = await ran_geometry(fx_kit)
        other, _ = await fx_kit.seed_scan_page(book_record, order_key='a1')
        assert other.id != page.id
        with pytest.raises(NotFoundError):
            await _marks().set(
                fx_open_book(actor, book_record.id), other.id, version.id, ResultNote(mark=ResultMark.GOOD, comment='')
            )

    async def test_a_comment_keeps_the_version_and_its_notes_through_a_collection(
        self, fx_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify a collection that follows a run deletes neither the version with a comment nor its mark and files.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_record, page, first = await ran_geometry(fx_kit)
        await _marks().set(
            fx_open_book(actor, book_record.id), page.id, first.id, ResultNote(mark=ResultMark.BAD, comment=COMMENT)
        )
        stronger = RecipeDraft(steps=[Step(processor_key=fx_kit.fake.spec.key, params={STRENGTH_PARAMETER: 2})])
        await fx_kit.edit_recipe(actor, book_record, Stage.GEOMETRY, stronger)
        await run_stage(fx_kit, actor, book_record, StageRun(stage=Stage.GEOMETRY))
        kept = await fx_kit.stored_version(book_record.id, first.id)
        async with fx_kit.files.assets(book_record.id).readable(BookKeys(book_record.id).version_directory(first)):
            pass
        assert (kept.mark, kept.comment) == (ResultMark.BAD, COMMENT)
