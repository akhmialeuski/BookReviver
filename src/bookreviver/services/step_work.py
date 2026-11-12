"""One step of a recipe on one page: what it reads, and the making of its version.

``StepSource`` is what a step reads, the version before it or the scan, and ``StepWork`` is the step itself on a page of
a run. Before the step is run its identifier is worked out from everything it depends on, so a version that was made
already is found and used again, and only a step whose parameters, edit or input changed is computed. A page that shows
a generated leaf passes every step unchanged: the version of the step holds the image of its input, flagged as skipped.

A version is stored by two short ``Book.change`` calls, the mark that it is running and then its result, so the viewer
shows the first results while the rest are made, and no transaction is held while the processor computes. Each of them
reads the page again, so a page that was deleted while its steps ran gets nothing written, and the files of a version
whose ``Book.change`` failed are discarded. A version that fails is stored as failed with its reason, so a repeated run
makes it again under the same identifier.

The step belongs to the ``PageWork`` of its page, which belongs to the ``StageWork`` of its job: the step reaches the
book, the runner and the processors through them, and nothing is handed down from call to call.
"""

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Self

from attrs import evolve, frozen

from bookreviver.domain.errors import ConflictError, DomainError
from bookreviver.domain.events import PageVersionReady
from bookreviver.domain.images import Rendition
from bookreviver.domain.keys import BookKeys
from bookreviver.domain.page_steps import PageStepKey
from bookreviver.domain.processors import ProcessorScope
from bookreviver.domain.sides import PageSide
from bookreviver.domain.spans import SpanAttribute, SpanName, StepOutcome
from bookreviver.domain.versions import PageSize, PageVersion, VersionData, VersionInputs, VersionScale, VersionState
from bookreviver.services.steps import StepRun

if TYPE_CHECKING:
    from bookreviver.core.plugins import Processor
    from bookreviver.domain.ids import PageVersionId, StorageKey
    from bookreviver.domain.metadata import MetadataMap
    from bookreviver.domain.page_steps import PageEdit
    from bookreviver.domain.recipes import Step
    from bookreviver.domain.sources import Scan
    from bookreviver.ports.persistence import BookRepositories
    from bookreviver.ports.tracing import Span
    from bookreviver.services.page_work import PageWork

UNEXPECTED_FAILURE: str = 'The step failed because of an unexpected error. It has been logged.'
SPLIT_NOT_AVAILABLE: str = 'The step {key} splits a scan, so it is the only step of its recipe and cannot be previewed.'
WRONG_OUTPUTS: str = 'The step {key} made {count} outputs, and a step of one page makes one.'

logger = logging.getLogger(__name__)


@frozen(kw_only=True)
class StepSource:
    """What a step reads: the version before it, or the scan, and the image of it.

    :ivar version_id: Version the step reads, or None when it reads a scan.
    :ivar image: Key of the image, the ``full`` or the ``preview`` of the version or the scan.
    :ivar data: ``data`` of the version, or the facts of the scan.
    :ivar scale: Whether the image is the full one or the preview, which is what the step reading it runs on.
    :ivar ratio: Size of the image over the size of the full image, 1 for a full run.
    :ivar book: What the book gives the step that reads the book for the fields its parameters leave to it, which lies
                under the settings of the page, or None for a run that has not worked it out.
    """

    version_id: PageVersionId | None
    image: StorageKey
    data: MetadataMap
    scale: VersionScale = VersionScale.FULL
    ratio: float = 1.0
    book: MetadataMap | None = None

    @staticmethod
    def preview_ratio(width_px: int, height_px: int, preview_long_side_px: int) -> float:
        """Work out the size of the preview of an image over its full size.

        :param width_px: Width of the full image.
        :type width_px: int
        :param height_px: Height of the full image.
        :type height_px: int
        :param preview_long_side_px: Longer side of a preview in pixels.
        :type preview_long_side_px: int
        :returns: The ratio, at most 1, since a preview is never larger than the image.
        :rtype: float
        """
        return min(1.0, preview_long_side_px / max(width_px, height_px))

    @classmethod
    def of_scan(cls, scan: Scan, scale: VersionScale, preview_long_side_px: int) -> Self:
        """Read a scan, whose image the page split reads.

        :param scan: The scan, whose renditions are ready.
        :type scan: Scan
        :param scale: Whether the full image or the preview is read.
        :type scale: VersionScale
        :param preview_long_side_px: Longer side of a preview in pixels.
        :type preview_long_side_px: int
        :returns: The source, with the facts of the scan for its data.
        :rtype: Self
        """
        facts = scan.facts
        if scale is VersionScale.PREVIEW:
            return cls(
                version_id=None,
                image=BookKeys.scan_rendition(scan, Rendition.PREVIEW),
                data=facts.as_data(),
                scale=scale,
                ratio=cls.preview_ratio(facts.width_px, facts.height_px, preview_long_side_px),
            )
        return cls(
            version_id=None,
            image=BookKeys.scan_rendition(scan, scan.renditions.full),
            data=facts.as_data(),
            scale=scale,
        )

    @classmethod
    def of_version(cls, version: PageVersion, scale: VersionScale, preview_long_side_px: int) -> Self:
        """Read a version of a page, which the first step of a stage reads.

        :param version: The version, whose images are ready.
        :type version: PageVersion
        :param scale: Whether the full image or the preview is read.
        :type scale: VersionScale
        :param preview_long_side_px: Longer side of a preview in pixels.
        :type preview_long_side_px: int
        :returns: The source, with the data of the version.
        :rtype: Self
        :raises ValueError: If the version has no image.
        """
        if scale is VersionScale.FULL:
            return cls(version_id=version.id, image=cls._image_of(version, scale), data=version.data, scale=scale)
        size = PageSize.from_data(version.data)
        ratio = 1.0 if size is None else cls.preview_ratio(size.width_px, size.height_px, preview_long_side_px)
        return cls(
            version_id=version.id, image=cls._image_of(version, scale), data=version.data, scale=scale, ratio=ratio
        )

    def after(self, version: PageVersion) -> Self:
        """Give what the next step of a chain reads once this source was made into a version.

        A preview made of a preview is as large of the full image as the preview it read, so the ratio goes on, and so
        does what the book gives.

        :param version: The version a step made of this source.
        :type version: PageVersion
        :returns: The source the next step reads, on the same scale.
        :rtype: Self
        :raises ValueError: If the version has no image.
        """
        return evolve(self, version_id=version.id, image=self._image_of(version, self.scale), data=version.data)

    @staticmethod
    def _image_of(version: PageVersion, scale: VersionScale) -> StorageKey:
        """Give the key of the image of a version a step reads on a scale.

        :param version: The version.
        :type version: PageVersion
        :param scale: Whether the full image or the preview is read.
        :type scale: VersionScale
        :returns: The key of the ``preview`` or of the ``full`` image.
        :rtype: StorageKey
        :raises ValueError: If the version has no image.
        """
        if version.renditions is None:
            err_msg = f'The version {version.id} has no image.'
            raise ValueError(err_msg)
        rendition = Rendition.PREVIEW if scale is VersionScale.PREVIEW else version.renditions.full
        return BookKeys.version_rendition(version, rendition)


@frozen
class StepWork:
    """One step of a recipe on one page of a run, which finds the version it made before or makes it.

    :ivar page_work: The work on the page the step belongs to, through which it reaches the book of the job.
    :ivar step: The step of the recipe.
    :ivar source: What the step reads.
    """

    page_work: PageWork
    step: Step
    source: StepSource

    @property
    def processor(self) -> Processor:
        """The processor of the step, from the catalogue of the job."""
        return self.page_work.work.processors.get(self.step.processor_key)

    @property
    def skipped(self) -> bool:
        """Whether the page is a leaf the program drew, which passes every step unchanged and unmarked."""
        return self.page_work.page.is_leaf

    async def make(self) -> PageVersion:
        """Make the version of the step, or return the one a run made before from the same inputs.

        :returns: The version, in the state ready or failed.
        :rtype: PageVersion
        :raises NotFoundError: If the processor is not in the catalogue, or the page was deleted.
        :raises InvalidParametersError: If the parameters do not fit the processor.
        :raises ConflictError: If the step is one that splits a scan, which this run cannot apply.
        """
        work = self.page_work.work
        if self.processor.spec.scope is ProcessorScope.SPLIT:
            raise ConflictError(SPLIT_NOT_AVAILABLE.format(key=self.step.processor_key))
        attributes = {
            SpanAttribute.STEP_TYPE: self.step.processor_key,
            SpanAttribute.STEP_SCALE: self.source.scale,
            SpanAttribute.STEP_OUTCOME: StepOutcome.FAILED,
        }
        with work.tracer.span(SpanName.STEP, attributes) as span:
            inputs, edit, existing = await work.book.read(self._look)
            if existing is not None and existing.state is VersionState.READY:
                span.set_attribute(SpanAttribute.STEP_OUTCOME, StepOutcome.CACHED)
                return existing
            version, cached = await work.book.change(lambda repos, _book_record: self._begin(repos, inputs))
            if cached:
                span.set_attribute(SpanAttribute.STEP_OUTCOME, StepOutcome.CACHED)
                return version
            run = StepRun(
                processor_key=self.step.processor_key,
                params=inputs.params,
                input_data=self.source.data,
                image=self.source.image,
                edit=edit,
                scale=self.source.scale,
                ratio=self.source.ratio,
                side=inputs.side,
                skipped=self.skipped,
            )
            return await self._store(await self._attempt(version, run), span)

    def _look(self, repos: BookRepositories) -> tuple[VersionInputs, PageEdit | None, PageVersion | None]:
        """Work out what the step depends on, and find the version an earlier run made of it, from one snapshot.

        The values of the page and of the parts of the book it is in are laid over the parameters of the step before
        they are checked, so the identifier of the version hashes the parameters the step runs with, and two pages that
        end up with equal ones share it. The side is part of what a step depends on, so a page moved to the other side
        of the book is made again.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :returns: What the version depends on, the manual edit of the step on the page, and the stored version with
                  that identifier, or None when no run has made it.
        :rtype: tuple[VersionInputs, PageEdit | None, PageVersion | None]
        :raises InvalidParametersError: If the parameters do not fit the processor.
        """
        page_work, step, source, processor = self.page_work, self.step, self.source, self.processor
        layers, skipped = page_work.layers, self.skipped
        key = PageStepKey(page_work.page.id, page_work.stage, step.step_id)
        state = None if skipped else repos.page_step_states.find(key)
        reads_book = source.book and page_work.work.book_steps.of(step) is not None
        base = {**step.params, **(source.book or {})} if reads_book else step.params
        laid = base if skipped else layers.lay(repos, step.step_id, base, None if state is None else state.params)
        checked = processor.validate_params(laid)
        edit = None if state is None else state.edit
        by_side = processor.spec.by_page_side and not skipped
        inputs = VersionInputs(
            page_id=page_work.page.id,
            processor=processor.spec.ref,
            params={} if skipped else checked,
            input_id=source.version_id,
            edit_hash='' if edit is None else edit.edit_hash,
            scale=source.scale,
            side=PageSide.of_position(layers.position(repos)) if by_side else None,
            skipped=skipped,
        )
        return inputs, edit, repos.page_versions.find(inputs.identify())

    def _begin(self, repos: BookRepositories, inputs: VersionInputs) -> tuple[PageVersion, bool]:
        """Mark the version as running, unless another change made it ready meanwhile.

        The earlier steps took time, so the page may be gone and the version may have been made by another change.
        The mark and the comment of a version that is made again stay the ones the user gave it.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :param inputs: What the version depends on.
        :type inputs: VersionInputs
        :returns: The version, and whether it was ready already, so nothing is left to run.
        :rtype: tuple[PageVersion, bool]
        :raises NotFoundError: If the page was deleted meanwhile.
        """
        page_work = self.page_work
        repos.pages.get(page_work.page.id)
        found = repos.page_versions.find(inputs.identify())
        if found is not None and found.state is VersionState.READY:
            return found, True
        running = PageVersion(
            id=inputs.identify(),
            page_id=page_work.page.id,
            stage=page_work.stage,
            processor=self.processor.spec.ref,
            input_id=self.source.version_id,
            params=inputs.params,
            renditions=None,
            state=VersionState.RUNNING,
            scale=self.source.scale,
            edit_hash=inputs.edit_hash,
            created_at=datetime.now(UTC) if found is None else found.created_at,
            mark=None if found is None else found.mark,
            comment='' if found is None else found.comment,
        )
        if found is None:
            repos.page_versions.add(running)
        else:
            repos.page_versions.update(running)
        return running, False

    async def _attempt(self, version: PageVersion, run: StepRun) -> PageVersion:
        """Run the step and store its files, turning what goes wrong into a failed version with its reason.

        :param version: The version being made, in the state running.
        :type version: PageVersion
        :param run: What the step reads.
        :type run: StepRun
        :returns: The version as ready, or as failed with the reason in its data.
        :rtype: PageVersion
        """
        try:
            return await self._execute(version, run)
        except DomainError as error:
            return evolve(version, state=VersionState.FAILED, data={VersionData.ERROR: str(error)})
        except Exception:
            logger.exception('The step %s on page %s failed', run.processor_key, version.page_id)
            return evolve(version, state=VersionState.FAILED, data={VersionData.ERROR: UNEXPECTED_FAILURE})

    async def _execute(self, version: PageVersion, run: StepRun) -> PageVersion:
        """Run the step of a version and store its files.

        :param version: The version being made, in the state running.
        :type version: PageVersion
        :param run: What the step reads.
        :type run: StepRun
        :returns: The version as ready.
        :rtype: PageVersion
        :raises ConflictError: If the step makes more or fewer than one output.
        :raises DomainError: If the step cannot run on its input.
        """
        work = self.page_work.work
        async with work.runner.execute(work.book, run) as result:
            if len(result.outputs) != 1:
                raise ConflictError(WRONG_OUTPUTS.format(key=run.processor_key, count=len(result.outputs)))
            return await work.runner.store(
                work.book, version, result.outputs[0], policy=work.book_record.image_policy, tiles=False
            )

    async def _store(self, made: PageVersion, span: Span) -> PageVersion:
        """Store the result of the step in a change of its own, and tell the browser of a version that is ready.

        The page is read again, so a page deleted while the step ran gets nothing written, and the files the step
        left are discarded when the change fails.

        :param made: The version as the step left it, ready or failed.
        :type made: PageVersion
        :param span: The span of the step, which learns that the step ran.
        :type span: Span
        :returns: The version as stored.
        :rtype: PageVersion
        :raises NotFoundError: If the page or the version was deleted meanwhile.
        """
        work, page_id = self.page_work.work, self.page_work.page.id

        def store(repos: BookRepositories) -> None:
            """Write the result over the running version, keeping the mark and the comment the user gave meanwhile.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :raises NotFoundError: If the page or the version was deleted meanwhile.
            """
            repos.pages.get(page_id)
            stored = repos.page_versions.get(made.id)
            repos.page_versions.update(evolve(made, mark=stored.mark, comment=stored.comment))

        try:
            await work.book.change(lambda repos, _book_record: store(repos))
        except Exception:
            await work.runner.discard(work.book, BookKeys.version_directory(made))
            raise
        if made.state is VersionState.READY:
            span.set_attribute(SpanAttribute.STEP_OUTCOME, StepOutcome.RAN)
            await work.book.events.publish(PageVersionReady(version=made))
        return made
