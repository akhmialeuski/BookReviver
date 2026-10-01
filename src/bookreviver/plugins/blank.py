"""A blank leaf: a white page of a given size, which a book lacks where the printed book has one.

``pages.blank`` reads no image. It draws a white page of the size and the resolution in its parameters with libvips,
as a 1-bit PNG, because a white page has two colours at most and so is a bilevel page whatever the project's image
policy.
``Image.black`` makes a black image of unsigned characters, so adding 255 makes it white and keeps the single band. The
resolution is written in pixels per millimetre. libvips always writes a resolution, so a leaf made without one carries
its default of one pixel per millimetre, and the resolution the book keeps is the one in the data of the version, which
says it is unknown.
"""

from typing import TYPE_CHECKING, override

import pyvips
from pydantic import Field

from bookreviver.domain.enums import ColorMode, ProcessorScope, Stage, VersionOutput
from bookreviver.domain.values import PageSize, ProcessorSpec
from bookreviver.plugins.base import ModelProcessor, Params
from bookreviver.ports.processing import StepOutput, StepResult

if TYPE_CHECKING:
    from bookreviver.ports.processing import StepInput

MM_PER_INCH: float = 25.4
WHITE: int = 255
# The depth of a pixel of a bilevel PNG in bits
BILEVEL_BIT_DEPTH: int = 1
BLANK_IMAGE_NAME: str = 'blank.png'


class BlankParams(Params):
    """The size of the leaf.

    :ivar width_px: Width of the leaf in pixels.
    :ivar height_px: Height of the leaf in pixels.
    :ivar dpi: Resolution of the leaf in dots per inch, or None when it is unknown.
    """

    width_px: int = Field(gt=0, title='Width', description='Width of the leaf in pixels')
    height_px: int = Field(gt=0, title='Height', description='Height of the leaf in pixels')
    dpi: float | None = Field(
        default=None, gt=0, title='Resolution', description='Resolution of the leaf in dots per inch, if known'
    )


class BlankPage(ModelProcessor):
    """Draws a white page of a given size."""

    params_model = BlankParams
    spec = ProcessorSpec(
        key='pages.blank',
        version='1',
        title='Blank leaf',
        stage=Stage.PAGE_ORDER,
        scope=ProcessorScope.PAGE,
        outputs=frozenset({VersionOutput.IMAGE}),
        parameters=BlankParams.model_json_schema(),
    )

    @override
    def run(self, step_input: StepInput) -> StepResult:
        """Write the white page into the work directory.

        :param step_input: The step, whose parameters give the size of the leaf.
        :type step_input: StepInput
        :returns: One bilevel output holding the leaf, with its size in the data.
        :rtype: StepResult
        """
        params = BlankParams.model_validate(step_input.params)
        leaf = (pyvips.Image.black(params.width_px, params.height_px) + WHITE).cast(pyvips.enums.BandFormat.UCHAR)
        if params.dpi is not None:
            per_mm = params.dpi / MM_PER_INCH
            leaf = leaf.copy(xres=per_mm, yres=per_mm)
        target = step_input.workdir / BLANK_IMAGE_NAME
        leaf.pngsave(str(target), bitdepth=BILEVEL_BIT_DEPTH)
        size = PageSize(width_px=params.width_px, height_px=params.height_px, dpi=params.dpi)
        return StepResult(outputs=[StepOutput(image=target, color_mode=ColorMode.BILEVEL, data=size.as_data())])
