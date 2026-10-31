"""Reading the sums of the stages of a book through a kit, in the transaction that only reads which they run in."""

from typing import TYPE_CHECKING

from bookreviver.domain.slices import SliceRequest
from tests.harness.builders.processing import open_book

if TYPE_CHECKING:
    from bookreviver.domain.ids import BookId, StepId
    from bookreviver.domain.library import BookOverview
    from bookreviver.domain.slices import Slice
    from bookreviver.domain.stage_summaries import StageRow, StageSummary
    from bookreviver.domain.stages import Stage
    from tests.harness.builders.processing import ProcessingKit


async def stage_rows(
    kit: ProcessingKit, book_id: BookId, stage: Stage, request: SliceRequest, step_id: StepId | None = None
) -> Slice[StageRow]:
    """Read a window of the rows of a stage of a book, as ``StageSummaries.rows`` gives them.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: The book.
    :type book_id: BookId
    :param stage: The stage.
    :type stage: Stage
    :param request: Offset and limit of the window of pages.
    :type request: SliceRequest
    :param step_id: A step of a recipe of the stage to place each page at, or None for the rows of the stage alone.
    :type step_id: StepId | None
    :returns: The rows of the window in book order, and the number of pages of the book.
    :rtype: Slice[StageRow]
    :raises NotFoundError: If no recipe of the stage has the step.
    """
    return await kit.stages().rows(await open_book(kit, book_id), stage, request, step_id)


async def book_summaries(kit: ProcessingKit, book_id: BookId) -> list[StageSummary]:
    """Sum every stage of a book, as ``StageSummaries.stages`` does.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: The book.
    :type book_id: BookId
    :returns: One summary for each stage, in the order of the pipeline.
    :rtype: list[StageSummary]
    """
    return list((await kit.stages().stages(await open_book(kit, book_id), SliceRequest())).items)


async def books_with_progress(kit: ProcessingKit, *books: BookId) -> list[BookOverview]:
    """Read the overviews of some books with their progress, as ``StageSummaries.overview`` gives them.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param books: The books, which may be none.
    :type books: BookId
    :returns: The overview of each book with its progress, in the order given.
    :rtype: list[BookOverview]
    """
    stages = kit.stages()

    async def overview_of(book_id: BookId) -> BookOverview:
        """Read the overview of one book with its progress.

        :param book_id: The book.
        :type book_id: BookId
        :returns: The overview with the progress.
        :rtype: BookOverview
        """
        return await stages.overview(await open_book(kit, book_id))

    return [await overview_of(book_id) for book_id in books]
