"""The summaries a client draws a book's stages from: the stage bar, the strip of pages, and the progress in the list.

The stage bar of a book needs the state of every stage over all its pages, and the strip of a stage needs the state of
each page, so asking page by page would take a request for each page. The counts here come from one grouped query of
the page stage records, which for a window of books is one query for all of them, and the number of pages that have
an image comes with the project itself. A stage no page has a record of is counted as not run, since the records are
written when a stage runs.

A stage is available when it is done by hand or a processor of it is installed, so a stage turns from soon to
available the day the first plugin of it is installed, with no change here. The class does not check who owns a
project: the service of the projects does that and hands over projects that are the actor's.
"""

from collections import defaultdict
from typing import TYPE_CHECKING

from attrs import evolve

from bookreviver.domain.enums import JobState, PageStageStatus, Stage
from bookreviver.domain.stage_summaries import BookProgress, StageRow, StageSummary
from bookreviver.domain.values import Slice

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from bookreviver.domain.entities import Project, ProjectOverview
    from bookreviver.domain.ids import ProjectId, RecipeId
    from bookreviver.domain.stage_summaries import StageTally, VariantTally
    from bookreviver.domain.values import SliceRequest
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.processing import ProcessorCatalog


class StageSummaries:
    """Sums the stages of books over their pages, from the records of the page stages."""

    def __init__(self, *, uow: UnitOfWork, catalogue: ProcessorCatalog) -> None:
        """Work over the ports of one request.

        :param uow: Unit of work of the request, which is only read.
        :type uow: UnitOfWork
        :param catalogue: The processors the application can run, which say which stages are available.
        :type catalogue: ProcessorCatalog
        """
        self._uow = uow
        self._catalogue = catalogue

    async def of_book(self, overview: ProjectOverview) -> list[StageSummary]:
        """Sum every stage of a book, in the order of the pipeline.

        :param overview: The book with the number of its pages that have an image.
        :type overview: ProjectOverview
        :returns: One summary for each stage, with the recipe the stage runs by when it has one.
        :rtype: list[StageSummary]
        """
        project_id = overview.project.id
        tallies = {tally.stage: tally for tally in await self._uow.page_stages.tally({project_id})}
        recipes = {recipe.stage: recipe.id for recipe in await self._uow.recipes.list_active(project_id)}
        variants: defaultdict[Stage, list[VariantTally]] = defaultdict(list)
        for tally in await self._uow.page_stages.variant_tally(project_id):
            variants[tally.stage].append(tally)
        return self._summarize(overview, tallies, recipes, variants)

    async def rows(self, project: Project, stage: Stage, request: SliceRequest) -> Slice[StageRow]:
        """Give a window of the pages of a book, in book order, with where each stands in a stage.

        :param project: The book.
        :type project: Project
        :param stage: The stage.
        :type stage: Stage
        :param request: Offset and limit of the window of pages.
        :type request: SliceRequest
        :returns: One row for each page of the window, and the number of pages of the book. A page the stage has not run
                  on has the status not run and no version.
        :rtype: Slice[StageRow]
        """
        pages = await self._uow.pages.list_for_project(project.id, request)
        records = {
            record.page_id: record
            for record in await self._uow.page_stages.list_for_pages([page.id for page in pages.items])
            if record.stage is stage
        }
        head_ids = {record.head_version_id for record in records.values() if record.head_version_id is not None}
        heads = {version.id: version for version in await self._uow.page_versions.list_by_ids(head_ids)}
        rows: list[StageRow] = []
        for page in pages.items:
            if (record := records.get(page.id)) is None:
                rows.append(StageRow(page_id=page.id))
                continue
            head = None if record.head_version_id is None else heads.get(record.head_version_id)
            rows.append(
                StageRow(
                    page_id=page.id,
                    status=PageStageStatus.of(record.state),
                    recipe_id=record.recipe_id,
                    pinned=record.pinned,
                    head_version=head,
                )
            )
        return Slice(items=rows, total=pages.total)

    async def with_progress(self, overviews: Sequence[ProjectOverview]) -> list[ProjectOverview]:
        """Add the progress of each book to its overview, with the same number of queries for any number of books.

        A stage is running while a job that runs it is queued or running, which is read for all the books at once.

        :param overviews: The books of a window of a list, or one book.
        :type overviews: Sequence[ProjectOverview]
        :returns: The overviews with their progress, in the same order.
        :rtype: list[ProjectOverview]
        """
        project_ids = {overview.project.id for overview in overviews}
        tallies: defaultdict[ProjectId, dict[Stage, StageTally]] = defaultdict(dict)
        for tally in await self._uow.page_stages.tally(project_ids):
            tallies[tally.project_id][tally.stage] = tally
        running: defaultdict[ProjectId, set[Stage]] = defaultdict(set)
        for job in await self._uow.jobs.list_for_projects(project_ids, JobState.active()):
            if job.stage is not None:
                running[job.project_id].add(job.stage)
        return [
            evolve(
                overview,
                progress=BookProgress.of(
                    self._summarize(overview, tallies[overview.project.id], {}, {}), running[overview.project.id]
                ),
            )
            for overview in overviews
        ]

    def _summarize(
        self,
        overview: ProjectOverview,
        tallies: Mapping[Stage, StageTally],
        recipes: Mapping[Stage, RecipeId],
        variants: Mapping[Stage, Sequence[VariantTally]],
    ) -> list[StageSummary]:
        """Sum every stage of a book from the counts already read.

        :param overview: The book with the number of its pages that have an image.
        :type overview: ProjectOverview
        :param tallies: The counts of the records of each stage that has any.
        :type tallies: Mapping[Stage, StageTally]
        :param recipes: The active recipe of each stage that has one.
        :type recipes: Mapping[Stage, RecipeId]
        :param variants: How many pages each recipe processed, for each stage that has any, or none for a list of
                         books, which does not show them.
        :type variants: Mapping[Stage, Sequence[VariantTally]]
        :returns: One summary for each stage, in the order of the pipeline.
        :rtype: list[StageSummary]
        """
        with_processor = {spec.stage for spec in self._catalogue.specs()}
        return [
            StageSummary.of(
                stage,
                available=stage.manual or stage in with_processor,
                pages=overview.image_page_count,
                tally=tallies.get(stage),
                active_recipe_id=recipes.get(stage),
            ).with_variants(variants.get(stage, ()))
            for stage in Stage
        ]
