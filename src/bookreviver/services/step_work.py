"""The steps of a recipe on one page: the chain of them, and the making of the version of each.

``StepChain`` is the steps a run or a preview goes over on a page. It makes the version of each step, each reading the
one before, and asks the book what it gives the step that reads it. ``StepWork`` is one step of it on the page. Before
the step is run its identifier is worked out from everything it depends on, so a version that was made already is found
and used again, and only a step whose parameters, edit or input changed is computed. A page that shows a generated leaf
passes every step unchanged: the version of the step holds the image of its input, flagged as skipped.

A version is stored by two short ``Book.change`` calls, the mark that it is running and then its result, so the viewer
shows the first results while the rest are made, and no transaction is held while the processor computes. Each of them
reads the page again, so a page that was deleted while its steps ran gets nothing written, and the files of a version
whose ``Book.change`` failed are discarded. A version that fails is stored as failed with its reason, so a repeated run
makes it again under the same identifier.

The chain belongs to the ``PageWork`` of its page, which belongs to the ``StageWork`` of its job, and the step to the
chain: the step reaches the book, the runner and the processors through them, and nothing is handed down from call to
call. This module imports ``page_work`` for them, and ``page_work`` never imports this one.
"""

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from attrs import evolve, frozen

from bookreviver.core.content import BookContent
from bookreviver.domain.errors import ConflictError, DomainError
from bookreviver.domain.events import PageVersionReady
from bookreviver.domain.keys import BookKeys
from bookreviver.domain.page_steps import PageStepKey
from bookreviver.domain.processors import ProcessorScope
from bookreviver.domain.sides import PageSide
from bookreviver.domain.spans import SpanAttribute, SpanName, StepOutcome
from bookreviver.domain.versions import PageVersion, VersionData, VersionInputs, VersionState
from bookreviver.services.steps import StepRun

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.core.plugins import Processor
    from bookreviver.domain.metadata import MetadataMap
    from bookreviver.domain.page_steps import PageEdit
    from bookreviver.domain.recipes import Step
    from bookreviver.ports.persistence import BookRepositories
    from bookreviver.ports.tracing import Span
    from bookreviver.services.page_work import PageWork
    from bookreviver.services.step_source import StepSource

UNEXPECTED_FAILURE: str = 'The step failed because of an unexpected error. It has been logged.'
SPLIT_NOT_AVAILABLE: str = 'The step {key} splits a scan, so it is the only step of its recipe and cannot be previewed.'
WRONG_OUTPUTS: str = 'The step {key} made {count} outputs, and a step of one page makes one.'

logger = logging.getLogger(__name__)


class StepChain:
    """The steps a run or a preview goes over on one page, which make a version each, every one reading the one before.

    :ivar page_work: The work on the page the steps belong to, through which the book of the job is reached.
    :ivar steps: The steps to make, in order.
    """

    def __init__(self, page_work: PageWork, steps: Sequence[Step]) -> None:
        """Take the steps of a page.

        :param page_work: The work on the page.
        :type page_work: PageWork
        :param steps: The steps a run or a preview goes over.
        :type steps: Sequence[Step]
        """
        self.page_work = page_work
        self.steps = steps

    async def make(self, source: StepSource) -> PageVersion | None:
        """Make the version of each step, each reading the one before, on the scale of the source.

        :param source: What the first step reads, whose scale is the scale of every version made and whose book
                       is laid over the step that takes one.
        :type source: StepSource
        :returns: The version of the last step, or the one that failed, which ends the chain, or None for no step.
        :rtype: PageVersion | None
        :raises DomainError: If a processor is missing or its parameters do not fit.
        """
        version: PageVersion | None = None
        for step in self.steps:
            version = await StepWork(self.page_work, step, source).make()
            # A task is done whether the version was made, found made, or failed, and no transaction is open here
            await self.page_work.work.control.task_done()
            if version.state is not VersionState.READY:
                return version
            source = source.after(version)
        return version

    async def measure(self, source: StepSource) -> PageVersion | None:
        """Make the steps as they stand, and give the version of the last one.

        The chain is the steps as far as the one that reads the book.

        :param source: What the first step reads.
        :type source: StepSource
        :returns: The version of the last step on the page, or None when a step failed on the way.
        :rtype: PageVersion | None
        """
        version = await self.make(source)
        return None if version is None or version.state is not VersionState.READY else version

    async def book_of(self, source: StepSource) -> MetadataMap:
        """Work out what the book gives the step that reads it, on a page that is run or previewed alone.

        :param source: What the first step reads.
        :type source: StepSource
        :returns: The fields of the step that are by the book with their values, none when the step has no field by the
                  book or no page has a result of it.
        :rtype: MetadataMap
        """
        work, steps = self.page_work.work, self.steps
        if (fields := work.engine.book_fields(steps)) is None:
            return {}
        made = await StepChain(self.page_work, steps[: fields.position + 1]).measure(source)
        fresh = {} if made is None else {self.page_work.page.id: made}
        stage = self.page_work.stage
        return await work.book.read(
            lambda repos: fields.book_step.from_book(BookContent(repos), stage, fields.params, fresh)
        )

    async def with_book(self, source: StepSource) -> StepSource:
        """Give the source with what the book gives the step that reads it, worked out for this page alone.

        :param source: What the first step reads.
        :type source: StepSource
        :returns: The source the chain of the steps starts from.
        :rtype: StepSource
        """
        return evolve(source, book=await self.book_of(source))


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
        return self.page_work.work.engine.processors.get(self.step.processor_key)

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
        with work.engine.tracer.span(SpanName.STEP, attributes) as span:
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
        reads_book = source.book and page_work.work.engine.book_steps.of(step) is not None
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
        async with work.engine.runner.execute(work.book, run) as result:
            if len(result.outputs) != 1:
                raise ConflictError(WRONG_OUTPUTS.format(key=run.processor_key, count=len(result.outputs)))
            return await work.engine.runner.store(
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
            await work.engine.runner.discard(work.book, BookKeys.version_directory(made))
            raise
        if made.state is VersionState.READY:
            span.set_attribute(SpanAttribute.STEP_OUTCOME, StepOutcome.RAN)
            await work.book.events.publish(PageVersionReady(version=made))
        return made
