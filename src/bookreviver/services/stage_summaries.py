"""The summaries a client draws a book's stages from: the stage bar, the strip of pages, and the progress in the list.

The stage bar of a book needs the state of every stage over all its pages, and the strip of a stage needs the state of
each page, so asking page by page would take a request for each page. The counts here come from one grouped query of
the page stage records of the book, and the number of pages that have an image comes with the book itself. A stage
no page has a record of is counted as not run, since the records are written when a stage runs.

A stage is available when it is done by hand or a processor of it is installed, so a stage turns from soon to
available the day the first plugin of it is installed, with no change here. The class does not check who owns a
book: the access dependency ``open_book`` of the API does that. Every method reads through one read transaction of the
``Book`` it is given.
"""

from collections import defaultdict
from typing import TYPE_CHECKING

from attrs import evolve

from bookreviver.domain.enums import PageStageStatus, Stage
from bookreviver.domain.stage_summaries import BookProgress, StageRow, StageSummary
from bookreviver.domain.values import Slice
from bookreviver.services.stage_inputs import StageInputs
from bookreviver.services.step_rows import StepRows

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

    from bookreviver.domain.entities import BookOverview, PageVersion, Recipe
    from bookreviver.domain.enums import RecipeKind
    from bookreviver.domain.ids import PageVersionId, StepId
    from bookreviver.domain.stage_summaries import StageTally, StepTally
    from bookreviver.domain.values import SliceRequest
    from bookreviver.ports.persistence import BookRepositories
    from bookreviver.ports.processing import ProcessorCatalog
    from bookreviver.services.book import Book


class StageSummaries:
    """Sums the stages of books over their pages, from the records of the page stages."""

    def __init__(self, *, processors: ProcessorCatalog) -> None:
        """Work over the ports of one request.

        :param processors: The processors the application can run, which say which stages are available.
        :type processors: ProcessorCatalog
        """
        self._processors = processors

    async def overview(self, book: Book) -> BookOverview:
        """Return a book with its counts and progress.

        A stage is running while a job that runs it is queued or running.

        :param book: The book to read.
        :type book: Book
        :returns: The book with its counts and progress.
        :rtype: BookOverview
        :raises NotFoundError: If the book is not stored.
        """

        def work(repos: BookRepositories) -> BookOverview:
            """Read the overview of the book and add the progress of its stages.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: The overview with its progress.
            :rtype: BookOverview
            """
            overview = repos.book_record.overview()
            tallies = {tally.stage: tally for tally in repos.page_stages.tally()}
            running = {job.stage for job in book.jobs.active() if job.stage is not None}
            summaries = self._summarize(
                image_page_count=overview.image_page_count, tallies=tallies, recipes={}, kinds={}, stopped={}
            )
            return evolve(overview, progress=BookProgress.of(summaries, running))

        return await book.read(work)

    async def stages(self, book: Book, request: SliceRequest) -> Slice[StageSummary]:
        """Sum every stage of a book over its pages, in the order of the pipeline, and cut a window of them.

        :param book: The book to read.
        :type book: Book
        :param request: Offset and limit of the window of stages.
        :type request: SliceRequest
        :returns: The window of the stages in the order of the pipeline, with the recipes of each kind of page when the
                  stage has them, and the number of all the stages.
        :rtype: Slice[StageSummary]
        :raises NotFoundError: If the book is not stored.
        """

        def work(repos: BookRepositories) -> Slice[StageSummary]:
            """Sum every stage of the book and cut the window the request asks for.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: The window of the stages and the number of all the stages.
            :rtype: Slice[StageSummary]
            """
            tallies = {tally.stage: tally for tally in repos.page_stages.tally()}
            recipes: defaultdict[Stage, list[Recipe]] = defaultdict(list)
            for recipe in repos.recipes.list_all():
                recipes[recipe.stage].append(recipe)
            kinds = repos.pages.kind_tally()
            stopped: defaultdict[Stage, list[StepTally]] = defaultdict(list)
            for step_tally in repos.page_stages.step_tally():
                stopped[step_tally.stage].append(step_tally)
            summaries = self._summarize(
                image_page_count=repos.book_record.overview().image_page_count,
                tallies=tallies,
                recipes=recipes,
                kinds=kinds,
                stopped=stopped,
            )
            return Slice(items=summaries[request.offset : request.offset + request.limit], total=len(summaries))

        return await book.read(work)

    async def rows(
        self,
        book: Book,
        stage: Stage,
        request: SliceRequest,
        step_id: StepId | None = None,
    ) -> Slice[StageRow]:
        """Give a window of the pages of a book, in book order, with where each stands in a stage.

        :param book: The book to read.
        :type book: Book
        :param stage: The stage.
        :type stage: Stage
        :param request: Offset and limit of the window of pages.
        :type request: SliceRequest
        :param step_id: A step of a recipe of the stage to place each page at, or None for the rows of the stage alone.
        :type step_id: StepId | None
        :returns: One row for each page of the window, and the number of pages of the book. A page the stage has not run
                  on has the status not run and no version. With a step each row also says what the step read and made
                  on the page and where its shape comes from. Every row has the picture that stands for the page at the
                  place asked for, whether the stage has run on the page or not.
        :rtype: Slice[StageRow]
        :raises NotFoundError: If the book is not stored, or no recipe of the stage has the step.
        """

        def work(repos: BookRepositories) -> Slice[StageRow]:
            """Place the pages of the window at the stage, and at the step when one is asked for.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: The rows of the window and the number of pages of the book.
            :rtype: Slice[StageRow]
            :raises NotFoundError: If no recipe of the stage has the step.
            """
            pages = repos.pages.list_window(request)
            records = {
                record.page_id: record
                for record in repos.page_stages.list_for_pages([page.id for page in pages.items])
                if record.stage is stage
            }
            head_ids = {record.head_version_id for record in records.values() if record.head_version_id is not None}
            heads = {version.id: version for version in repos.page_versions.list_by_ids(head_ids)}
            markers = self._markers(repos, heads.values())
            steps = (
                {}
                if step_id is None
                else StepRows(processors=self._processors, stage=stage, step_id=step_id).of(
                    repos, pages.items, records, heads
                )
            )
            reads = StageInputs.of(repos, [page.id for page in pages.items], stage)
            rows: list[StageRow] = []
            for page in pages.items:
                record = records.get(page.id)
                head = None if record is None or record.head_version_id is None else heads.get(record.head_version_id)
                step = steps.get(page.id)
                # A page that has nothing of its own at the place asked for is drawn from what the stage reads
                own = head if step is None else step.preceding
                picture = reads.get(page.id) if own is None else own
                if record is None:
                    rows.append(StageRow(page_id=page.id, kind=page.recipe_kind, step=step, picture=picture))
                    continue
                rows.append(
                    StageRow(
                        page_id=page.id,
                        status=PageStageStatus.of(record.state),
                        recipe_id=record.recipe_id,
                        kind=page.recipe_kind,
                        head_version=head,
                        through_step=record.through_step,
                        review_processor=None if head is None else markers.get(head.id),
                        step=step,
                        picture=picture,
                    )
                )
            return Slice(items=rows, total=pages.total)

        return await book.read(work)

    @staticmethod
    def _markers(repos: BookRepositories, heads: Collection[PageVersion]) -> dict[PageVersionId, str]:
        """Find the processor of the first step of a stage that marked each page that carries a review mark.

        A step keeps the mark of the step before it, so the mark of a current version is walked back through the
        versions it was read from, a level at a time for all the pages together, to the first that has it. A mark that
        an earlier stage made leads out of the stage and names no step.

        :param repos: Repositories of the transaction the caller runs in, which are only read.
        :type repos: BookRepositories
        :param heads: The current versions of the stage over a window of pages.
        :type heads: Collection[PageVersion]
        :returns: The key of the processor of the marking step, by the identifier of the current version. A version
                  without a mark, or whose mark an earlier stage made, has no entry.
        :rtype: dict[PageVersionId, str]
        """
        walking = {head.id: head for head in heads if head.review is not None}
        markers: dict[PageVersionId, str] = {}
        while walking:
            inputs = {
                version.id: version
                for version in repos.page_versions.list_by_ids(
                    {origin.input_id for origin in walking.values() if origin.input_id is not None}
                )
                if version.review is not None
            }
            ahead: dict[PageVersionId, PageVersion] = {}
            for head_id, origin in walking.items():
                earlier = None if origin.input_id is None else inputs.get(origin.input_id)
                if earlier is None:
                    markers[head_id] = origin.processor.key
                elif earlier.stage is origin.stage:
                    ahead[head_id] = earlier
            walking = ahead
        return markers

    def _summarize(
        self,
        *,
        image_page_count: int,
        tallies: Mapping[Stage, StageTally],
        recipes: Mapping[Stage, Sequence[Recipe]],
        kinds: Mapping[RecipeKind, int],
        stopped: Mapping[Stage, Sequence[StepTally]],
    ) -> list[StageSummary]:
        """Sum every stage of a book from the counts already read.

        :param image_page_count: The number of pages of the book that have an image.
        :type image_page_count: int
        :param tallies: The counts of the records of each stage that has any.
        :type tallies: Mapping[Stage, StageTally]
        :param recipes: The recipes of each stage that has any, or none for a list of books, which does not show them.
        :type recipes: Mapping[Stage, Sequence[Recipe]]
        :param kinds: How many pages with an image each kind has in the book.
        :type kinds: Mapping[RecipeKind, int]
        :param stopped: How many pages stopped at each step, for each stage that has any, or none for a list of books.
        :type stopped: Mapping[Stage, Sequence[StepTally]]
        :returns: One summary for each stage, in the order of the pipeline.
        :rtype: list[StageSummary]
        """
        with_processor = {spec.stage for spec in self._processors.specs()}
        return [
            StageSummary.of(
                stage,
                available=stage.manual or stage in with_processor,
                pages=image_page_count,
                tally=tallies.get(stage),
            )
            .with_recipes(recipes.get(stage, ()), kinds)
            .with_stopped(stopped.get(stage, ()))
            for stage in Stage
        ]
