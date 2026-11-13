"""The work of one job on one book, which the run of a stage and the preview of a step have in common.

``StageWork`` holds what every page and every step of the job needs: the open book, its record, the control of the
job and the ports, so the ``PageWork`` of a page and the ``StepChain`` of its steps reach them through the work they
belong to. ``StageRuntime`` is what a job works with apart from the book, and ``BookFields`` the step of a run whose
plugin reads the book, which the work finds among the steps of a recipe.

This module is the lowest of the chain: ``page_work`` and ``step_work`` import it, and it imports neither.
"""

from typing import TYPE_CHECKING

from attrs import field, frozen, validators

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
    from bookreviver.ports.ordering import OrderKeys
    from bookreviver.ports.runtime import JobControl
    from bookreviver.ports.tracing import Tracer
    from bookreviver.services.steps import StepRunner


@frozen(kw_only=True)
class StageRuntime:
    """What a run of a stage works with, apart from the book of the job.

    :ivar runner: Runner of processors and writer of their files.
    :ivar processors: The processors the application can run.
    :ivar order_keys: Builder of the order key of the right half of a spread, which a split inserts after the left.
    :ivar preview_long_side_px: Longer side of a preview in pixels, from which the ratio of a preview is worked out.
    :ivar tracer: Tracer the runs of pages and their steps are timed with.
    :ivar parallel_pages: Largest number of pages a run processes at the same time, which is as many as the executor
                          has places for full runs, so a run of a book uses every one of them.
    """

    runner: StepRunner
    processors: ProcessorCatalog
    order_keys: OrderKeys
    preview_long_side_px: int
    tracer: Tracer
    parallel_pages: int = field(default=1, validator=validators.ge(1))


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


class StageWork:
    """The work of one job on one book, which a run of a recipe and a preview have in common.

    It holds what every page and every step of the job needs, so the ``PageWork`` of a page and the ``StepWork`` of a
    step reach it through the work they belong to.

    :ivar book: The open book, whose transactions are opened for each version and never held while a step runs.
    :ivar book_record: Record of the book, whose image policy chooses the format of a version's ``full``.
    :ivar control: What the job reports through: every step of a page is a task, and the job stops between two.
    :ivar runner: Runner of processors and writer of their files.
    :ivar processors: The processors the application can run.
    :ivar preview_long_side_px: Longer side of a preview in pixels.
    :ivar tracer: Tracer the steps are timed with.
    :ivar book_steps: Finder of the steps whose plugin reads the whole book.
    """

    def __init__(self, *, book: Book, book_record: BookRecord, runtime: StageRuntime, control: JobControl) -> None:
        """Work over the ports of one job.

        :param book: The open book of the job.
        :type book: Book
        :param book_record: Record of the book.
        :type book_record: BookRecord
        :param runtime: The runner, the processors and the size of a preview.
        :type runtime: StageRuntime
        :param control: What the job reports through.
        :type control: JobControl
        """
        self.book = book
        self.book_record = book_record
        self.control = control
        self.runner = runtime.runner
        self.processors = runtime.processors
        self.preview_long_side_px = runtime.preview_long_side_px
        self.tracer = runtime.tracer
        self.book_steps = BookSteps(runtime.processors)

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

    async def refresh(self, page: Page, stage: Stage) -> None:
        """Bring a stale earlier stage of a page up to date before a step reads it; a preview leaves it as it is.

        :param page: Page whose earlier stage is stale.
        :type page: Page
        :param stage: The stale stage.
        :type stage: Stage
        """
