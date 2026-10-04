"""Fake processors for the tests of the catalogue and of the processing service, which need no image library."""

from typing import TYPE_CHECKING, override

from attrs import evolve

from bookreviver.domain.enums import (
    ColorMode,
    EditorKind,
    ProcessorScope,
    Stage,
    VersionData,
    VersionOutput,
    WorkerPool,
)
from bookreviver.domain.errors import ConflictError, InvalidParametersError
from bookreviver.domain.values import OrderRule, ProcessorSpec
from bookreviver.ports.processing import Processor, StepOutput, StepResult

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from bookreviver.domain.enums import ContentType
    from bookreviver.domain.values import MetadataMap
    from bookreviver.ports.processing import StepInput

# The marker a fake processor writes into the data of its output, so a test can tell which step ran
RAN_KEY: str = 'ran'
FAILING_PARAMETER: str = 'fail'
STRENGTH_PARAMETER: str = 'strength'
KNOWN_PARAMETERS: frozenset[str] = frozenset({STRENGTH_PARAMETER, FAILING_PARAMETER})


class FakeProcessor(Processor):
    """A processor of the geometry stage that copies its input and records that it ran.

    It reads the parameter ``strength``, which defaults to 1, and fails with a ``ConflictError`` when ``fail`` is true.
    """

    spec = ProcessorSpec(
        key='geometry.fake',
        version='1',
        title='Fake',
        stage=Stage.GEOMETRY,
        scope=ProcessorScope.PAGE,
        outputs=frozenset({VersionOutput.IMAGE}),
        editor=EditorKind.ROTATION,
        pool=WorkerPool.CPU,
    )

    def __init__(self) -> None:
        """Start with no run counted.

        :ivar runs: Steps run, by the full run.
        :ivar previews: Steps run by a preview.
        """
        self.runs = 0
        self.previews = 0

    @override
    def validate_params(self, raw: MetadataMap) -> MetadataMap:
        """Accept the parameters ``strength`` and ``fail`` and fill in the default strength.

        :param raw: Parameters to check.
        :type raw: MetadataMap
        :returns: The parameters with the default strength.
        :rtype: MetadataMap
        :raises InvalidParametersError: If a parameter is not known.
        """
        if unknown := set(raw) - KNOWN_PARAMETERS:
            err_msg = f'Unknown parameters {sorted(unknown)}.'
            raise InvalidParametersError(err_msg)
        return {STRENGTH_PARAMETER: 1, FAILING_PARAMETER: False, **raw}

    @override
    def run(self, step_input: StepInput) -> StepResult:
        """Return the input image unchanged, or fail when the parameters ask for it.

        :param step_input: What the step reads.
        :type step_input: StepInput
        :returns: One output holding the input image.
        :rtype: StepResult
        :raises ConflictError: If the parameter ``fail`` is true.
        """
        self.runs += 1
        return self._result(step_input)

    @override
    def preview(self, step_input: StepInput) -> StepResult:
        """Return the input image unchanged, counting the preview apart from the runs.

        :param step_input: What the step reads.
        :type step_input: StepInput
        :returns: One output holding the input image.
        :rtype: StepResult
        :raises ConflictError: If the parameter ``fail`` is true.
        """
        self.previews += 1
        return self._result(step_input)

    @staticmethod
    def _result(step_input: StepInput) -> StepResult:
        """Build the result of a step, or fail when the parameters ask for it.

        :param step_input: What the step reads.
        :type step_input: StepInput
        :returns: One output holding the input image.
        :rtype: StepResult
        :raises ConflictError: If the parameter ``fail`` is true.
        """
        if step_input.params[FAILING_PARAMETER]:
            err_msg = 'The fake step was told to fail.'
            raise ConflictError(err_msg)
        image: Path | None = step_input.image
        return StepResult(
            outputs=[
                StepOutput(
                    image=image, color_mode=ColorMode.GRAY, data={RAN_KEY: step_input.params[STRENGTH_PARAMETER]}
                )
            ]
        )


class GpuProcessor(FakeProcessor):
    """A processor of the recognition stage that needs a GPU worker."""

    spec = ProcessorSpec(
        key='recognition.fake',
        version='1',
        title='Fake recognition',
        stage=Stage.RECOGNITION,
        outputs=frozenset({VersionOutput.TEXT}),
        pool=WorkerPool.GPU,
    )


class MisnamedProcessor(FakeProcessor):
    """A processor whose key differs from the name it is registered under in the tests."""


class CleanupProcessor(FakeProcessor):
    """A processor of the cleanup stage, which the stage after geometry runs."""

    spec = ProcessorSpec(
        key='cleanup.fake',
        version='1',
        title='Fake cleanup',
        stage=Stage.CLEANUP,
        scope=ProcessorScope.PAGE,
        outputs=frozenset({VersionOutput.IMAGE}),
        editor=EditorKind.BRUSH_MASK,
    )


class AutoSplitProcessor(FakeProcessor):
    """A stand-in for ``split.auto``, which a test of the recipes and the queues needs in the catalogue without OpenCV."""

    spec = ProcessorSpec(
        key='split.auto',
        version='1',
        title='Fake automatic split',
        stage=Stage.PAGE_SPLIT,
        scope=ProcessorScope.SPLIT,
        outputs=frozenset({VersionOutput.IMAGE}),
        editor=EditorKind.SPLIT,
    )


class FakeContentProbe(Processor):
    """A stand-in for ``pages.content`` that answers what a test tells it to, one answer for each page it reads.

    :ivar answers: What the next pages are found to show, in the order they are read, where None is a page the processor
                   cannot read.
    :ivar reads: How many pages it was asked about.
    """

    spec = ProcessorSpec(
        key='pages.content',
        version='1',
        title='Content type',
        stage=Stage.PAGE_ORDER,
        scope=ProcessorScope.PAGE,
        outputs=frozenset(),
    )

    def __init__(self, answers: Sequence[ContentType | None]) -> None:
        """Start with the answers to give.

        :param answers: What the pages are found to show, in the order they are read, or None for a page that cannot be
                        read.
        :type answers: Sequence[ContentType | None]
        """
        self.answers = list(answers)
        self.reads = 0

    @override
    def validate_params(self, raw: MetadataMap) -> MetadataMap:
        """Accept no parameter.

        :param raw: Parameters to check.
        :type raw: MetadataMap
        :returns: The parameters.
        :rtype: MetadataMap
        :raises InvalidParametersError: If any parameter is given.
        """
        if raw:
            err_msg = f'Unknown parameters {sorted(raw)}.'
            raise InvalidParametersError(err_msg)
        return {}

    @override
    def run(self, step_input: StepInput) -> StepResult:
        """Give the next answer, or fail for a page that cannot be read.

        :param step_input: What the step reads.
        :type step_input: StepInput
        :returns: One output without an image, whose data hold the content type.
        :rtype: StepResult
        :raises ConflictError: If the next answer is None.
        """
        self.reads += 1
        answer = self.answers.pop(0)
        if answer is None:
            err_msg = 'The fake probe was told that the page cannot be read.'
            raise ConflictError(err_msg)
        return StepResult(
            outputs=[StepOutput(color_mode=ColorMode.GRAY, data={VersionData.CONTENT_TYPE: answer.value})]
        )


# The processors of the tests of the order of steps: each declares one place relative to the one before it
OPENING_KEY: str = 'geometry.opening'
FIRST_KEY: str = 'geometry.first'
SECOND_KEY: str = 'geometry.second'
THIRD_KEY: str = 'geometry.third'
OPENING_REASON: str = 'Opening sets the page up for First, so it usually comes before First.'
SECOND_REASON: str = 'Second reads what First leaves, so it usually comes after First.'
THIRD_REASON: str = 'Third works on what Second leaves, so it cannot come before Second.'


class OpeningProcessor(FakeProcessor):
    """A processor that usually stands before ``geometry.first``."""

    spec = evolve(
        FakeProcessor.spec,
        key=OPENING_KEY,
        title='Opening',
        before=(OrderRule(processor_key=FIRST_KEY, reason=OPENING_REASON),),
    )


class FirstProcessor(FakeProcessor):
    """A processor that asks for no place."""

    spec = evolve(FakeProcessor.spec, key=FIRST_KEY, title='First')


class SecondProcessor(FakeProcessor):
    """A processor that usually stands after ``geometry.first``."""

    spec = evolve(
        FakeProcessor.spec,
        key=SECOND_KEY,
        title='Second',
        after=(OrderRule(processor_key=FIRST_KEY, reason=SECOND_REASON),),
    )


class ThirdProcessor(FakeProcessor):
    """A processor that must stand after ``geometry.second``."""

    spec = evolve(
        FakeProcessor.spec,
        key=THIRD_KEY,
        title='Third',
        requires_after=(OrderRule(processor_key=SECOND_KEY, reason=THIRD_REASON),),
    )
