"""Flattening a page that is bent, as a page of a thick book is where it curves into the gutter.

``geometry.dewarp`` finds how the lines of a page are bent, which neither a turn nor a perspective straightens, and
remaps the page so that they are straight. The method says how the bend is found, and the parameters of each method are
its own:

* ``text-lines`` finds the lines of text, fits a cubic curve through the middle of each, and makes every line straight
  at the height it has at the middle of the page. A page with fewer than ``min_lines`` lines is left as it is and
  marked for review. A page whose lines are still further from straight than ``max_residual`` after the fit is
  flattened and marked for review.
* ``page-edges`` does the same with the top and the bottom edge of the sheet, as the mode ``marginal`` of ScanTailor
  does, for pages with an illustration and too few lines of text. It needs a background that parts from the paper.
* ``uvdoc`` asks the UVDoc network, which is downloaded the first time it is used.

``DewarpMethod.DOCRES`` is declared and not offered: its network needs a graphics card, which the plugin of the optional
group ``gpu`` brings.

A page whose lines are bent by less than ``min_bend`` pixels for each thousand of its width has no bend worth
correcting, and is left as it is with the review reason ``not-applied``, so a flat page is not spoiled by a needless
resampling. The bend, the number of lines, the residual and a summary of the curves go into the data of the version. A
``mesh`` edit of the user, two curves or a grid of them, replaces the search and has the confidence 1 and no minimum
of bend.

The image is remapped by ``FlatteningField``, and the mesh file it writes is stored by the runner of steps, which gives
the version the transform ``mesh(key)``.
"""

from typing import TYPE_CHECKING, Annotated, Literal, override

from pydantic import ConfigDict, Field, RootModel, Tag

from bookreviver.domain.enums import (
    DewarpMethod,
    ProcessorScope,
    ReviewReason,
    Stage,
    VersionData,
    VersionOutput,
)
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.geometry import Mesh
from bookreviver.domain.values import OrderRule, ProcessorSpec
from bookreviver.plugins.base import METHOD_TITLE, ModelProcessor, Params, method_discriminator
from bookreviver.plugins.cv_image import (
    MANUAL_CONFIDENCE,
    NO_IMAGE,
    color_mode_of,
    image_data,
    read_samples,
    settle_review,
    source_size_data,
    write_png,
)
from bookreviver.plugins.dewarp_methods import PageEdgesMethod, TextLinesMethod, UvDocMethod
from bookreviver.plugins.mesh_warp import Flattening, FlatteningField, Unfound
from bookreviver.plugins.uvdoc import UvDocModel
from bookreviver.ports.processing import StepOutput, StepResult

if TYPE_CHECKING:
    from bookreviver.domain.enums import ColorMode
    from bookreviver.plugins.cv_image import Samples
    from bookreviver.ports.processing import ProcessorSettings, StepInput

DEWARPED_IMAGE_NAME: str = 'dewarped.png'
MESH_FILE_NAME: str = 'mesh.json'
NOT_CONFIGURED: str = 'The method uvdoc needs the models directory of the settings, which this processor was not given.'


class BendParams(Params):
    """How much of a bend is worth correcting, whichever way it is found.

    :ivar min_bend: Bend below which the page is left as it is.
    """

    min_bend: float = Field(
        default=2.0,
        ge=0,
        le=100,
        title='Least bend',
        description='Bend, in pixels for each thousand of the width of the page, below which the page is left as it is',
    )


class ResidualParams(BendParams):
    """How crooked the lines may still be after the page is flattened, for the methods that follow curves.

    :ivar max_residual: Residual above which the page is marked for review.
    """

    max_residual: float = Field(
        default=3.0,
        gt=0,
        le=100,
        title='Greatest residual',
        description='How far the lines may still be from straight, in pixels for each thousand of the width of the '
        'page, before the page is marked for review',
    )


class TextLinesParams(ResidualParams):
    """The bend found by the curves of the lines of text.

    :ivar method: The lines of text.
    :ivar min_lines: Fewest lines the bend is taken from.
    """

    model_config = ConfigDict(title='Lines of text')

    method: Literal[DewarpMethod.TEXT_LINES] = Field(
        default=DewarpMethod.TEXT_LINES,
        title=METHOD_TITLE,
        description='A curve is fitted through the middle of each line of text, and every line is made straight',
    )
    min_lines: int = Field(
        default=5,
        ge=2,
        le=100,
        title='Fewest lines',
        description='Fewest lines of text the bend is taken from, or the page is left as it is and marked for review',
    )


class PageEdgesParams(ResidualParams):
    """The bend found by the top and the bottom edge of the sheet.

    :ivar method: The edges of the sheet.
    """

    model_config = ConfigDict(title='Top and bottom edges of the sheet')

    method: Literal[DewarpMethod.PAGE_EDGES] = Field(
        default=DewarpMethod.PAGE_EDGES,
        title=METHOD_TITLE,
        description='The top and the bottom edge of the sheet are made straight, for pages with few lines of text',
    )


class UvDocParams(BendParams):
    """The bend found by the UVDoc network.

    :ivar method: The UVDoc network.
    """

    model_config = ConfigDict(title='UVDoc network')

    method: Literal[DewarpMethod.UVDOC] = Field(
        default=DewarpMethod.UVDOC,
        title=METHOD_TITLE,
        description='A neural network predicts how the page is bent; it is downloaded the first time it is used',
    )


DewarpChoice = Annotated[
    Annotated[TextLinesParams, Tag(DewarpMethod.TEXT_LINES)]
    | Annotated[PageEdgesParams, Tag(DewarpMethod.PAGE_EDGES)]
    | Annotated[UvDocParams, Tag(DewarpMethod.UVDOC)],
    method_discriminator(DewarpMethod.TEXT_LINES),
]


class DewarpParams(RootModel[DewarpChoice]):
    """The method of the search and its parameters, the lines of text when none is named."""


class Dewarp(ModelProcessor):
    """Remaps a bent page so that its lines are straight."""

    params_model = DewarpParams
    spec = ProcessorSpec(
        key='geometry.dewarp',
        version='1',
        title='Dewarp',
        summary='Straightens lines bent into the gutter',
        stage=Stage.GEOMETRY,
        scope=ProcessorScope.PAGE,
        outputs=frozenset({VersionOutput.IMAGE}),
        parameters=DewarpParams.model_json_schema(),
        editor=Mesh.editor,
        after=(
            OrderRule(
                processor_key='geometry.perspective',
                reason=(
                    'Dewarp measures how the lines bend on an upright sheet, so it usually comes after Perspective.'
                ),
            ),
            OrderRule(
                processor_key='geometry.deskew',
                reason=(
                    'Dewarp measures the bend of the lines on a page that is turned level, '
                    'so it usually comes after Deskew.'
                ),
            ),
        ),
    )

    def __init__(self) -> None:
        """Start without the network of the method ``uvdoc``, which ``configure`` makes ready."""
        self._uvdoc: UvDocModel | None = None

    @override
    def configure(self, settings: ProcessorSettings) -> None:
        """Take the models directory, where the network of the method ``uvdoc`` is kept.

        :param settings: What the application tells the processor about the machine.
        :type settings: ProcessorSettings
        """
        super().configure(settings)
        self._uvdoc = UvDocModel(settings.models_dir)

    @override
    def run(self, step_input: StepInput) -> StepResult:
        """Find how the page is bent and remap it flat.

        :param step_input: The image of the page, the parameters, and the mesh edit if there is one.
        :type step_input: StepInput
        :returns: One output holding the flat page, or the page as it was when it is not bent or the bend is not found.
        :rtype: StepResult
        :raises ConflictError: If there is no image, it cannot be read, or the network is needed and cannot be loaded.
        """
        if step_input.image is None:
            raise ConflictError(NO_IMAGE.format(key=self.spec.key))
        params = DewarpParams.model_validate(step_input.params).root
        image = read_samples(step_input.image)
        color_mode = color_mode_of(image, step_input.input_data)
        data = image_data(image, step_input.input_data, color_mode) | source_size_data(image, step_input.scale)
        shape = None if step_input.edit is None else step_input.edit.geometry
        manual = shape.scaled(step_input.scale) if isinstance(shape, Mesh) else None
        found: Flattening | Unfound
        if manual is not None:
            found = Flattening(
                field=FlatteningField.from_mesh(manual, image.shape[1]),
                mesh=manual,
                lines=len(manual.rows),
                confidence=MANUAL_CONFIDENCE,
            )
        else:
            found = self._search(params, image)
        if isinstance(found, Unfound):
            data |= {VersionData.LINES: found.lines, VersionData.CONFIDENCE: 0.0, VersionData.SKIPPED: True}
            self._record_mesh(data, found.mesh, step_input.scale)
            return self._unchanged(step_input, color_mode, data, found.reason)
        bend = found.field.bend()
        data |= {VersionData.BEND: bend, VersionData.LINES: found.lines}
        if found.residual is not None:
            data[VersionData.RESIDUAL] = found.residual
        if found.confidence is not None:
            data[VersionData.CONFIDENCE] = found.confidence
        self._record_mesh(data, found.mesh, step_input.scale)
        if manual is None and bend < params.min_bend:
            data[VersionData.SKIPPED] = True
            return self._unchanged(step_input, color_mode, data, ReviewReason.NOT_APPLIED)
        target = step_input.workdir / DEWARPED_IMAGE_NAME
        write_png(found.field.remap(image, color_mode), target)
        mesh_file = step_input.workdir / MESH_FILE_NAME
        found.field.write(image.shape[0], mesh_file)
        data[VersionData.SKIPPED] = False
        review = settle_review(data, found.review, step_input.input_data)
        return StepResult(
            outputs=[StepOutput(image=target, color_mode=color_mode, data=data, review=review, mesh=mesh_file)]
        )

    def _search(self, params: TextLinesParams | PageEdgesParams | UvDocParams, image: Samples) -> Flattening | Unfound:
        """Find how the page is bent by the method of the step.

        :param params: The parameters of the step, which name its method.
        :type params: TextLinesParams | PageEdgesParams | UvDocParams
        :param image: The samples of the page.
        :type image: Samples
        :returns: What the method found, or why it found nothing.
        :rtype: Flattening | Unfound
        :raises ConflictError: If the method is ``uvdoc`` and the processor was not given a models directory.
        """
        match params:
            case TextLinesParams():
                return TextLinesMethod(image, min_lines=params.min_lines, max_residual=params.max_residual).find()
            case PageEdgesParams():
                return PageEdgesMethod(image, max_residual=params.max_residual).find()
            case _:
                if self._uvdoc is None:
                    raise ConflictError(NOT_CONFIGURED)
                return UvDocMethod(image, self._uvdoc).find()

    @staticmethod
    def _record_mesh(data: dict[str, object], mesh: Mesh | None, scale: float) -> None:
        """Put the summary of the curves in the data, in the pixels of the full image, for the editor to start from.

        :param data: Data of the version being made, to which the summary is added.
        :type data: dict[str, object]
        :param mesh: The summary in the pixels of the image the step read, or None when there is none.
        :type mesh: Mesh | None
        :param scale: Size of the image the step read over the size of the full image.
        :type scale: float
        """
        if mesh is not None:
            data[VersionData.MESH] = mesh.scaled(1 / scale).to_data()

    @staticmethod
    def _unchanged(
        step_input: StepInput, color_mode: ColorMode, data: dict[str, object], reason: ReviewReason
    ) -> StepResult:
        """Give the result of a page that is left as it was, marked for review.

        :param step_input: The image of the page and the data of its input version.
        :type step_input: StepInput
        :param color_mode: Colour mode of the page.
        :type color_mode: ColorMode
        :param data: Data of the version being made.
        :type data: dict[str, object]
        :param reason: Why the page is left as it was.
        :type reason: ReviewReason
        :returns: One output holding the image of the input.
        :rtype: StepResult
        """
        review = settle_review(data, reason, step_input.input_data)
        return StepResult(outputs=[StepOutput(image=step_input.image, color_mode=color_mode, data=data, review=review)])
