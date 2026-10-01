"""The page split that is skipped: the page is the whole scan, and its base version is a copy of it.

``split.none`` reads the ``full`` image of the scan and returns it as it is, so the service stores it as the page's own
copy in the format the project's image policy asks for, and a JPEG scan that is stored as a JPEG is copied byte for
byte.
It needs no OpenCV. The facts of the scan arrive as the input data of the step, since the scan is the input of a base
version, and the size of the image goes into the data of the version, which gives a blank leaf its median size.
"""

from typing import TYPE_CHECKING, override

from bookreviver.domain.enums import ColorMode, ProcessorScope, Stage, VersionOutput
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.values import PageSize, ProcessorSpec
from bookreviver.plugins.base import ModelProcessor, Params
from bookreviver.ports.processing import StepOutput, StepResult

if TYPE_CHECKING:
    from bookreviver.ports.processing import StepInput

NO_SCAN_IMAGE: str = 'The step split.none needs the image of a scan.'


class NoParams(Params):
    """``split.none`` has no parameters."""


class SplitNone(ModelProcessor):
    """Makes the whole scan the page, unchanged."""

    params_model = NoParams
    spec = ProcessorSpec(
        key='split.none',
        version='1',
        title='Whole scan',
        stage=Stage.PAGE_SPLIT,
        scope=ProcessorScope.PAGE,
        outputs=frozenset({VersionOutput.IMAGE}),
        parameters=NoParams.model_json_schema(),
    )

    @override
    def run(self, step_input: StepInput) -> StepResult:
        """Return the image of the scan as the image of the page.

        :param step_input: The ``full`` image of the scan and the facts of the scan as the input data.
        :type step_input: StepInput
        :returns: One output holding the scan's own file, its colour mode, and its size in the data.
        :rtype: StepResult
        :raises ConflictError: If the step has no image to copy.
        """
        if step_input.image is None:
            raise ConflictError(NO_SCAN_IMAGE)
        facts = step_input.input_data
        size = PageSize(width_px=facts['width_px'], height_px=facts['height_px'], dpi=facts.get('dpi'))
        output = StepOutput(
            image=step_input.image,
            color_mode=ColorMode(facts['color_mode']),
            data=size.as_data(),
        )
        return StepResult(outputs=[output])
