"""The work of one job on one book, which the run of a stage and the preview of a step have in common.

``StageEngine`` is what runs the steps of every job, built once: the runner, the processors, the tracer and the bounds
of a run, which also tells how many tasks the steps of a page make and which step leaves a field to the book
(``BookFields``). ``StageWork`` is the work of one job on one book: it belongs to the engine and holds the open book,
its record and the control of the job, so the ``PageWork`` of a page and the ``StepChain`` of its steps reach the book
and the ports through the work they belong to.

This module is the lowest of the chain: ``page_work``, ``step_work``, ``spread_splits`` and ``stage_runs`` import it,
and it imports none of them. ``StageRuns`` of ``stage_runs``, which opens the work of a job, lies on top of them.
"""

from typing import TYPE_CHECKING

from attrs import frozen

from bookreviver.services.book_steps import BookSteps

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.core.book import Book
    from bookreviver.core.plugins import BookStep, ProcessorCatalog
    from bookreviver.domain.books import BookRecord
    from bookreviver.domain.metadata import MetadataMap
    from bookreviver.domain.pages import Page
    from bookreviver.domain.recipes import Step
    from bookreviver.domain.stages import Stage
    from bookreviver.ports.runtime import JobControl
    from bookreviver.ports.tracing import Tracer
    from bookreviver.services.job_starter import ProcessingConfig
    from bookreviver.services.steps import StepRunner


@frozen(kw_only=True)
class BookFields:
    """The step of a run that reads the book, whose parameters leave a field to the book.

    :ivar position: Place of the step among the steps the run goes over.
    :ivar book_step: The half of the plugin of the step that reads the book.
    :ivar params: Parameters of the step with the defaults of the processor filled in.
    """

    position: int
    book_step: BookStep
    params: MetadataMap


class StageEngine:
    """What runs the steps of recipes on the pages of a job, the same for every job, so the container builds it once.

    It answers what the steps of a run ask of the processors: how many tasks they make on a page, and which of them
    leaves a field to the book. The ``StageWork`` of a job belongs to it and reaches the runner, the processors and the
    tracer through it.

    :ivar runner: Runner of processors and writer of their files.
    :ivar processors: The processors the application can run.
    :ivar tracer: Tracer the runs of pages and their steps are timed with.
    :ivar preview_long_side_px: Longer side of a preview in pixels, from which the ratio of a preview is worked out.
    :ivar parallel_pages: Largest number of pages a run processes at the same time, which is as many as the executor
                          has places for full runs, so a run of a book uses every one of them.
    :ivar book_steps: Finder of the steps whose plugin reads the whole book.
    """

    def __init__(
        self, *, runner: StepRunner, processors: ProcessorCatalog, tracer: Tracer, config: ProcessingConfig
    ) -> None:
        """Run steps through the processors of the application.

        :param runner: Runner of processors and writer of their files.
        :type runner: StepRunner
        :param processors: The processors the application can run.
        :type processors: ProcessorCatalog
        :param tracer: Tracer the runs of pages and their steps are timed with.
        :type tracer: Tracer
        :param config: The bounds of processing, of which the size of a preview and the pages run together are read.
        :type config: ProcessingConfig
        """
        self.runner = runner
        self.processors = processors
        self.tracer = tracer
        self.preview_long_side_px = config.preview_long_side_px
        self.parallel_pages = config.parallel_pages
        self.book_steps = BookSteps(processors)

    def task_count(self, steps: Sequence[Step]) -> int:
        """Count the tasks of the steps a run or a preview goes over on one page.

        Every step is a task. A page with a step that leaves a field to the book is also taken as far as that step once
        more, for the plugin to read what the step found on it, so those steps are tasks again.

        :param steps: The steps the run goes over, which are on.
        :type steps: Sequence[Step]
        :returns: The number of tasks the page makes.
        :rtype: int
        """
        fields = self.book_fields(steps)
        return len(steps) + (0 if fields is None else fields.position + 1)

    def book_fields(self, steps: Sequence[Step]) -> BookFields | None:
        """Find the first step among steps whose plugin reads the book, when its parameters leave a field to the book.

        :param steps: The steps a run or a preview goes over.
        :type steps: Sequence[Step]
        :returns: The place of the step, the half of its plugin that reads the book and its parameters with the
                  defaults filled in, or None when no step reads the book, or every field of it has a value.
        :rtype: BookFields | None
        """
        position = self.book_steps.position(steps)
        book_step = None if position is None else self.book_steps.of(steps[position])
        if position is None or book_step is None:
            return None
        step = steps[position]
        params = self.processors.get(step.processor_key).validate_params(step.params)
        if not book_step.leaves_to_book(params):
            return None
        return BookFields(position=position, book_step=book_step, params=params)


class StageWork:
    """The work of one job on one book, which a run of a recipe and a preview have in common.

    It holds what every page and every step of the job needs, so the ``PageWork`` of a page and the ``StepWork`` of a
    step reach it through the work they belong to.

    :ivar engine: What runs the steps, through which the runner, the processors and the tracer are reached.
    :ivar book: The open book, whose transactions are opened for each version and never held while a step runs.
    :ivar book_record: Record of the book, whose image policy chooses the format of a version's ``full``.
    :ivar control: What the job reports through: every step of a page is a task, and the job stops between two.
    """

    def __init__(self, *, engine: StageEngine, book: Book, book_record: BookRecord, control: JobControl) -> None:
        """Work over the ports of one job.

        :param engine: What runs the steps of the job.
        :type engine: StageEngine
        :param book: The open book of the job.
        :type book: Book
        :param book_record: Record of the book.
        :type book_record: BookRecord
        :param control: What the job reports through.
        :type control: JobControl
        """
        self.engine = engine
        self.book = book
        self.book_record = book_record
        self.control = control

    async def refresh(self, page: Page, stage: Stage) -> None:
        """Bring a stale earlier stage of a page up to date before a step reads it; a preview leaves it as it is.

        :param page: Page whose earlier stage is stale.
        :type page: Page
        :param stage: The stale stage.
        :type stage: Stage
        """
