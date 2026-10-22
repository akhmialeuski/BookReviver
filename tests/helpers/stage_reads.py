"""Reading the sums of the stages of a book through a kit, in the transaction that only reads which they run in."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bookreviver.domain.entities import Project, ProjectOverview
    from bookreviver.domain.enums import Stage
    from bookreviver.domain.ids import StepId
    from bookreviver.domain.stage_summaries import StageRow, StageSummary
    from bookreviver.domain.values import Slice, SliceRequest
    from tests.helpers.processing import ProcessingKit


async def stage_rows(
    kit: ProcessingKit, project: Project, stage: Stage, request: SliceRequest, step_id: StepId | None = None
) -> Slice[StageRow]:
    """Read a window of the rows of a stage of a book, as ``StageSummaries.rows`` gives them.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param project: The book.
    :type project: Project
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
    return await kit.uow().read_book(project.id, lambda repos: stages.rows(repos, stage, request, step_id))


async def book_summaries(kit: ProcessingKit, project: Project) -> list[StageSummary]:
    """Sum every stage of a book, as ``StageSummaries.of_book`` does.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param project: The book.
    :type project: Project
    :returns: One summary for each stage, in the order of the pipeline.
    :rtype: list[StageSummary]
    """
    stages = kit.stages()
    return await kit.uow().read_book(project.id, lambda repos: stages.of_book(repos, repos.projects.overview(project)))


async def books_with_progress(kit: ProcessingKit, *projects: Project) -> list[ProjectOverview]:
    """Read the overviews of some books with their progress, as ``StageSummaries.with_progress`` gives them.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param projects: The books, which may be none.
    :type projects: Project
    :returns: The overview of each book with its progress, in the order given.
    :rtype: list[ProjectOverview]
    """
    stages = kit.stages()

    async def overview_of(project: Project) -> ProjectOverview:
        """Read the overview of one book with its progress.

        :param project: The book.
        :type project: Project
        :returns: The overview with the progress.
        :rtype: ProjectOverview
        """
        return await kit.uow().read_book(
            project.id, lambda repos: stages.with_progress(repos, repos.projects.overview(project))
        )

    return [await overview_of(project) for project in projects]
