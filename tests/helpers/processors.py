"""Fake processors for the tests of the catalogue and of the processing service, which need no image library."""

from typing import TYPE_CHECKING, override

from bookreviver.domain.enums import ColorMode, EditorKind, ProcessorScope, Stage, VersionOutput, WorkerPool
from bookreviver.domain.errors import ConflictError, InvalidParametersError
from bookreviver.domain.values import ProcessorSpec
from bookreviver.ports.processing import Processor, StepOutput, StepResult

if TYPE_CHECKING:
    from pathlib import Path

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
