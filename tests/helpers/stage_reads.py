"""Reading the sums of the stages of a book through a kit, in the transaction that only reads which they run in."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bookreviver.domain.entities import BookOverview
    from bookreviver.domain.enums import Stage
    from bookreviver.domain.ids import BookId, StepId
    from bookreviver.domain.stage_summaries import StageRow, StageSummary
    from bookreviver.domain.values import Slice, SliceRequest
    from tests.helpers.processing import ProcessingKit


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
    stages = kit.stages()
    return await kit.databases.open(book_id).read(lambda repos: stages.rows(repos, stage, request, step_id))


async def book_summaries(kit: ProcessingKit, book_id: BookId) -> list[StageSummary]:
    """Sum every stage of a book, as ``StageSummaries.of_book`` does.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: The book.
    :type book_id: BookId
    :returns: One summary for each stage, in the order of the pipeline.
    :rtype: list[StageSummary]
    """
    stages = kit.stages()
    return await kit.databases.open(book_id).read(
        lambda repos: stages.of_book(repos, repos.book_record.overview(book_id))
    )


async def books_with_progress(kit: ProcessingKit, *books: BookId) -> list[BookOverview]:
    """Read the overviews of some books with their progress, as ``StageSummaries.with_progress`` gives them.

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
        return await kit.databases.open(book_id).read(
            lambda repos: stages.with_progress(repos, repos.book_record.overview(book_id))
        )

    return [await overview_of(book_id) for book_id in books]
