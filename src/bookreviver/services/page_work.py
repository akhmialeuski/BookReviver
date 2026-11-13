"""The work of one stage on one page of a job: what the stage reads on the page, and the chain of its steps.

``PageWork`` holds the page and the stage, so the methods that find the input of the stage, make the versions of its
steps and ask the book what it gives a step take only what differs from call to call. It belongs to the ``StageWork``
of its job, through which it reaches the book, the runner and the processors, and it owns a ``StepWork`` for each step
it makes.

A stage reads the current version of the nearest earlier stage of the page, or the scan itself for the page split. An
earlier stage that is stale, or stopped short of its last step, is brought up to date first, which a run does by its
own recipe and a preview leaves as it is.

A step whose plugin has a half that reads the book (``BookStep``) may leave some of its parameters to the book, such as
the page size every page is placed on, which no page alone knows. What the book gives is laid over the parameters of
the step: a page that is run or previewed alone is taken as far as the step first, and the plugin is asked with the
version that made, beside what the other pages hold.
"""

from typing import TYPE_CHECKING

from attrs import evolve, frozen

from bookreviver.core.content import BookContent
from bookreviver.core.pages import BookPage
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.pages import PageOrigin
from bookreviver.domain.stages import PageStageKey, Stage, StageState
from bookreviver.domain.versions import VersionState
from bookreviver.services.step_params import PageLayers
from bookreviver.services.step_work import StepSource, StepWork

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.core.plugins import BookStep
    from bookreviver.domain.metadata import MetadataMap
    from bookreviver.domain.pages import Page
    from bookreviver.domain.recipes import Step
    from bookreviver.domain.stages import PageStage
    from bookreviver.domain.versions import PageVersion, VersionScale
    from bookreviver.ports.persistence import BookRepositories
    from bookreviver.services.stage_runs import StageWork

EARLIER_STAGE_FAILED: str = (
    'The {stage} stage of the page is out of date and could not be run again, so a later stage cannot read it.'
)


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


class PageWork:
    """One stage on one page of a job: the input of the stage, the chain of its steps and what the book gives them.

    :ivar work: The work of the job the page belongs to, through which the book and its ports are reached.
    :ivar page: The page being processed.
    :ivar stage: The stage of the steps.
    :ivar layers: The values of the parts of the book and the place of the page, read once for all the steps.
    """

    def __init__(self, work: StageWork, page: Page, stage: Stage) -> None:
        """Work on a page for a job.

        :param work: The work of the job the page belongs to.
        :type work: StageWork
        :param page: Page to process.
        :type page: Page
        :param stage: Stage of the steps.
        :type stage: Stage
        """
        self.work = work
        self.page = page
        self.stage = stage
        self.layers = PageLayers(page)

    async def source(self, scale: VersionScale) -> StepSource | None:
        """Find what the first step of the stage reads: the scan for the page split, else the nearest earlier version.

        An earlier stage that is stale or run through some of its steps only is brought up to date first, nearest stage
        first and no farther back than the stage that has a result to read, and the version is then picked by the rule
        of ``BookPages.inputs``, the one the rows of the stage draw their pictures by.

        :param scale: Whether the full images or the previews are read.
        :type scale: VersionScale
        :returns: The source, or None when the page has no image to process, such as a placeholder.
        :rtype: StepSource | None
        :raises ConflictError: If an earlier stage of the page is stale and running it again failed.
        """
        if self.stage is Stage.PAGE_SPLIT:
            return await self._scan_source(scale)
        await self._refresh_earlier()
        page_id, stage = self.page.id, self.stage
        read = await self.work.book.read(lambda repos: BookContent(repos).pages.inputs(stage, [page_id]).get(page_id))
        if read is None:
            return None
        return StepSource.of_version(read, scale, self.work.preview_long_side_px)

    async def chain(self, steps: Sequence[Step], source: StepSource) -> PageVersion | None:
        """Make the version of each step, each reading the one before, on the scale of the source.

        :param steps: The steps to make, in order.
        :type steps: Sequence[Step]
        :param source: What the first step reads, whose scale is the scale of every version made and whose book
                       is laid over the step that takes one.
        :type source: StepSource
        :returns: The version of the last step, or the one that failed, which ends the chain, or None for no step.
        :rtype: PageVersion | None
        :raises DomainError: If a processor is missing or its parameters do not fit.
        """
        version: PageVersion | None = None
        for step in steps:
            version = await StepWork(self, step, source).make()
            # A task is done whether the version was made, found made, or failed, and no transaction is open here
            await self.work.control.task_done()
            if version.state is not VersionState.READY:
                return version
            source = source.after(version)
        return version

    async def measure(self, steps: Sequence[Step], source: StepSource) -> PageVersion | None:
        """Make the steps up to the one that reads the book as they stand, and give the version that step made.

        :param steps: The steps a run or a preview goes over, as far as the one that reads the book.
        :type steps: Sequence[Step]
        :param source: What the first step reads.
        :type source: StepSource
        :returns: The version of the step on the page, or None when a step failed on the way.
        :rtype: PageVersion | None
        """
        version = await self.chain(steps, source)
        return None if version is None or version.state is not VersionState.READY else version

    async def book_of(self, steps: Sequence[Step], source: StepSource) -> MetadataMap:
        """Work out what the book gives the step that reads it, on a page that is run or previewed alone.

        :param steps: The steps a run or a preview goes over.
        :type steps: Sequence[Step]
        :param source: What the first step reads.
        :type source: StepSource
        :returns: The fields of the step that are by the book with their values, none when the step has no field by the
                  book or no page has a result of it.
        :rtype: MetadataMap
        """
        if (fields := self.work.book_fields(steps)) is None:
            return {}
        made = await self.measure(steps[: fields.position + 1], source)
        fresh = {} if made is None else {self.page.id: made}
        stage = self.stage
        return await self.work.book.read(
            lambda repos: fields.book_step.from_book(BookContent(repos), stage, fields.params, fresh)
        )

    async def with_book(self, steps: Sequence[Step], source: StepSource) -> StepSource:
        """Give the source with what the book gives the step that reads it, worked out for this page alone.

        :param steps: The steps a run or a preview goes over.
        :type steps: Sequence[Step]
        :param source: What the first step reads.
        :type source: StepSource
        :returns: The source the chain of the steps starts from.
        :rtype: StepSource
        """
        return evolve(source, book=await self.book_of(steps, source))

    async def _refresh_earlier(self) -> None:
        """Bring the earlier stages of the page up to date, nearest first, as far back as the one that can be read.

        A stage the page has not been through, or that has no current version, is passed by. A stage that is stale, or
        run through some of its steps only, is not ready to be read and is run through the rest first.

        :raises ConflictError: If an earlier stage of the page is stale and running it again failed.
        """
        for earlier in self.stage.earlier:
            record, readable = await self._earlier(earlier)
            if record is None or record.head_version_id is None:
                continue
            if record.state is StageState.STALE or record.through_step_id is not None:
                record, readable = await self._brought_up_to_date(earlier)
            # Stages further back are not brought up to date once a nearer one has a result to read
            if record.head_version_id is not None and readable:
                return

    async def _brought_up_to_date(self, earlier: Stage) -> tuple[PageStage, bool]:
        """Run an earlier stage of the page again, and read its record and whether its current version can be read.

        :param earlier: The earlier stage, stale or run through some of its steps only.
        :type earlier: Stage
        :returns: The record after the run, and whether its current version has an image to read.
        :rtype: tuple[PageStage, bool]
        :raises NotFoundError: If the record of the stage is gone after the run.
        :raises ConflictError: If running the stage again failed.
        """
        await self.work.refresh(self.page, earlier)
        record, readable = await self._earlier(earlier)
        if record is None:
            raise NotFoundError(PageStageKey(self.page.id, earlier))
        if record.state is StageState.FAILED:
            raise ConflictError(EARLIER_STAGE_FAILED.format(stage=earlier.label))
        return record, readable

    async def _earlier(self, earlier: Stage) -> tuple[PageStage | None, bool]:
        """Read the record of an earlier stage of the page, and tell whether its current version has an image to read.

        :param earlier: The earlier stage.
        :type earlier: Stage
        :returns: The record, or None when the page has not been through the stage, and whether the current version it
                  names has its images published, which is False for a record without a current version.
        :rtype: tuple[PageStage | None, bool]
        """
        page = self.page

        def work(repos: BookRepositories) -> tuple[PageStage | None, bool]:
            """Read the record and the version it names from one snapshot.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: The record and whether its current version has an image to read.
            :rtype: tuple[PageStage | None, bool]
            """
            in_book = BookPage(page, repos)
            head = in_book.head(earlier)
            return in_book.stage(earlier), head is not None and head.has_image

        return await self.work.book.read(work)

    async def _scan_source(self, scale: VersionScale) -> StepSource | None:
        """Find the scan the page was cut from, whose image the page split reads.

        :param scale: Whether the full image or the preview is read.
        :type scale: VersionScale
        :returns: The source, or None for a page that has no scan or whose scan is not cut yet.
        :rtype: StepSource | None
        :raises NotFoundError: If the scan was deleted.
        """
        if self.page.origin is not PageOrigin.SCAN or self.page.scan_id is None:
            return None
        scan_id = self.page.scan_id
        scan = await self.work.book.read(lambda repos: repos.scans.get(scan_id))
        if not scan.renditions.ready:
            return None
        return StepSource.of_scan(scan, scale, self.work.preview_long_side_px)
