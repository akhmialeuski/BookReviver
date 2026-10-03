"""Making a page black and white, or bringing its light level, by a method chosen in the form of the step.

``cleanup.binarize`` is the first step of the Cleanup stage. It reads the page the Geometry stage left and writes a page
of the ``mode`` the user chose:

- ``bw``: every pixel is ink or paper;
- ``gray`` and ``color``: the page keeps its tones and its light is levelled, so the paper is white wherever the scan
  was lit unevenly;
- ``mixed``: the text is ink or paper and the pictures keep their tones, as ScanTailor does. The pictures are found by
  their tones, and a ``regions`` edit adds a zone the search missed or removes one it took for a picture.

The threshold of a method that works from the neighbourhood of a pixel (Sauvola, Wolf, ISauvola, Su, Gatos, NICK,
Bradley) is worked out by the Doxa library, ``doxapy``, and so is the one threshold of Otsu. The parameters a method
has are those of the form: ``window`` and ``k``. A JSON Schema ``oneOf`` over ``method`` shows a form only the fields
of the method chosen, and the parameters are checked by the model of that method.

``thickness`` moves the line between ink and paper to either side, so the strokes come out thinner or thicker, and
``smooth`` rounds the staircase of the edge. Both are done on the ink the method found: the ink is blurred by a
fraction of a pixel and cut at a level, which is what a shifted threshold does to a stroke, whatever the method is.
``output_dpi`` makes a black and white page at a higher resolution than the scan, which cuts the thin serifs of an old
type at their true place and not on the grid of the scan. The scan is enlarged first and the threshold is worked out on
the enlarged page, with the window enlarged in step. A page whose resolution is not known is not enlarged, and neither
is a preview, which is made to be fast.
"""

from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Final, Literal, override

import cv2
import doxapy
import numpy as np
from pydantic import ConfigDict, Field, RootModel, Tag, field_validator

from bookreviver.domain.enums import (
    BinarizationMethod,
    ColorMode,
    EditorKind,
    OutputMode,
    ProcessorScope,
    Stage,
    TransformKind,
    VersionData,
    VersionOutput,
)
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.geometry import Regions, Transform
from bookreviver.domain.values import PageSize, ProcessorSpec
from bookreviver.plugins.base import METHOD_KEY, METHOD_TITLE, ModelProcessor, Params, labelled, method_discriminator
from bookreviver.plugins.cv_image import (
    BLACK,
    COLOR_PLANES,
    NO_IMAGE,
    WHITE,
    OtsuSplit,
    content_frame_of,
    image_data,
    read_samples,
    settle_review,
    source_size_data,
    write_png,
)
from bookreviver.plugins.pictures import PictureZones, level_light
from bookreviver.ports.processing import StepOutput, StepResult

if TYPE_CHECKING:
    from numpy.typing import NDArray

    from bookreviver.plugins.cv_image import Samples
    from bookreviver.ports.processing import StepInput

BINARIZED_IMAGE_NAME: str = 'binarized.png'
# What the thickness slider spans, each way, and how far the level the blurred ink is cut at moves across it
THICKNESS_LIMIT: int = 50
THICKNESS_REACH: float = 0.8
# The level the blurred ink is cut at when the strokes are left as they are
NEUTRAL_CUT: float = 0.5
# The blur in pixels of the scan that rounds the edge, and the one that only moves it, which is gentler so a thin
# stroke survives
SMOOTH_SIGMA_PX: float = 1.0
SHIFT_SIGMA_PX: float = 0.7
# The least window of a method and the factor beyond which a page is not enlarged, which a mistaken resolution could ask
MIN_WINDOW_PX: int = 3
MAX_WINDOW_PX: int = 301
MAX_ENLARGEMENT: float = 4.0
# Zero keeps the resolution of the scan, and no scan is made below the least resolution
KEEP_RESOLUTION: int = 0
MIN_OUTPUT_DPI: int = 72
MAX_OUTPUT_DPI: int = 1_200
# The names the parameters of the form have, and the words of the form that several methods share
WINDOW_PARAM: Final[str] = 'window'
K_PARAM: Final[str] = 'k'
METHOD_DESCRIPTION: Final[str] = 'How the ink is parted from the paper'
K_TITLE: Final[str] = 'Coefficient k'
DUMP_MODE: Final[str] = 'json'
DEFAULT_METHOD: Final[BinarizationMethod] = BinarizationMethod.SAUVOLA

# The algorithm of Doxa that each method offered by the step is
ALGORITHMS: Final[dict[BinarizationMethod, doxapy.Binarization.Algorithms]] = {
    BinarizationMethod.OTSU: doxapy.Binarization.Algorithms.OTSU,
    BinarizationMethod.SAUVOLA: doxapy.Binarization.Algorithms.SAUVOLA,
    BinarizationMethod.WOLF: doxapy.Binarization.Algorithms.WOLF,
    BinarizationMethod.ISAUVOLA: doxapy.Binarization.Algorithms.ISAUVOLA,
    BinarizationMethod.SU: doxapy.Binarization.Algorithms.SU,
    BinarizationMethod.GATOS: doxapy.Binarization.Algorithms.GATOS,
    BinarizationMethod.NICK: doxapy.Binarization.Algorithms.NICK,
    BinarizationMethod.BRADLEY: doxapy.Binarization.Algorithms.BRADLEY,
}


def _method(method: BinarizationMethod) -> Any:
    """Declare the field that names the method of an option of the form, which is set by choosing the option.

    :param method: The method of the option.
    :type method: BinarizationMethod
    :returns: The field, with the method as its only value.
    :rtype: Any
    """
    return Field(default=method, title=METHOD_TITLE, description=METHOD_DESCRIPTION)


class CommonParams(Params):
    """What every method shares: what the page becomes, and how its strokes and its resolution come out.

    :ivar mode: What the page is made into.
    :ivar thickness: How much thinner (below zero) or thicker (above zero) the strokes come out.
    :ivar smooth: Whether the staircase of the edges is rounded.
    :ivar output_dpi: Resolution in dots per inch of a black and white page, or zero to keep the one of the scan.
    """

    mode: Annotated[OutputMode, labelled(OutputMode)] = Field(
        default=OutputMode.BW, title='Output', description='What the page is made into'
    )
    thickness: int = Field(
        default=0,
        ge=-THICKNESS_LIMIT,
        le=THICKNESS_LIMIT,
        title='Stroke thickness',
        description='Thinner strokes below zero, thicker above',
    )
    smooth: bool = Field(default=False, title='Smooth edges', description='Round the staircase of the edges of letters')
    output_dpi: int = Field(
        default=KEEP_RESOLUTION,
        ge=KEEP_RESOLUTION,
        le=MAX_OUTPUT_DPI,
        title='Output resolution, dpi',
        description=f'Resolution of a black and white page, {KEEP_RESOLUTION} keeps the one of the scan',
    )

    @field_validator('output_dpi')
    @classmethod
    def _is_a_resolution(cls, value: int) -> int:
        """Check that the resolution is zero or one a scan can have.

        :param value: The resolution asked for.
        :type value: int
        :returns: The resolution.
        :rtype: int
        :raises ValueError: If it is above zero and below the least resolution.
        """
        if KEEP_RESOLUTION < value < MIN_OUTPUT_DPI:
            err_msg = f'The resolution is {KEEP_RESOLUTION} or at least {MIN_OUTPUT_DPI} dpi, not {value}.'
            raise ValueError(err_msg)
        return value


class WindowParams(CommonParams):
    """A method that works from the neighbourhood of a pixel.

    :ivar window: Side of the neighbourhood in pixels of the scan.
    """

    window: int = Field(
        default=41,
        ge=MIN_WINDOW_PX,
        le=MAX_WINDOW_PX,
        title='Window, px',
        description='Side of the neighbourhood the threshold is worked out in, in pixels of the scan',
    )


class SensitiveParams(WindowParams):
    """A method of the neighbourhood that has the weight of the deviation of the tones as its coefficient.

    :ivar k: The coefficient.
    """

    k: float = Field(
        default=0.2,
        ge=-1,
        le=1,
        title=K_TITLE,
        description='How far under the mean of the neighbourhood the threshold lies, more for a larger value',
    )


class OtsuParams(CommonParams):
    """Otsu, one threshold for the page."""

    model_config = ConfigDict(title='Otsu')

    method: Literal[BinarizationMethod.OTSU] = _method(BinarizationMethod.OTSU)


class SauvolaParams(SensitiveParams):
    """Sauvola, a threshold for each neighbourhood."""

    model_config = ConfigDict(title='Sauvola')

    method: Literal[BinarizationMethod.SAUVOLA] = _method(BinarizationMethod.SAUVOLA)


class WolfParams(SensitiveParams):
    """Wolf, Sauvola with the contrast of the whole page."""

    model_config = ConfigDict(title='Wolf')

    method: Literal[BinarizationMethod.WOLF] = _method(BinarizationMethod.WOLF)


class ISauvolaParams(SensitiveParams):
    """ISauvola, Sauvola for a stained page."""

    model_config = ConfigDict(title='ISauvola')

    method: Literal[BinarizationMethod.ISAUVOLA] = _method(BinarizationMethod.ISAUVOLA)


class GatosParams(SensitiveParams):
    """Gatos, a surface of the background under the ink."""

    model_config = ConfigDict(title='Gatos')

    method: Literal[BinarizationMethod.GATOS] = _method(BinarizationMethod.GATOS)


class NickParams(SensitiveParams):
    """NICK, for pages of pale ink, whose coefficient is below zero."""

    model_config = ConfigDict(title='NICK')

    method: Literal[BinarizationMethod.NICK] = _method(BinarizationMethod.NICK)
    k: float = Field(
        default=-0.2,
        ge=-1,
        le=1,
        title=K_TITLE,
        description='How far under the mean of the neighbourhood the threshold lies, more below zero',
    )


class SuParams(WindowParams):
    """Su, which follows the edges of the strokes."""

    model_config = ConfigDict(title='Su')

    method: Literal[BinarizationMethod.SU] = _method(BinarizationMethod.SU)


class BradleyParams(WindowParams):
    """Bradley, the mean of the neighbourhood."""

    model_config = ConfigDict(title='Bradley')

    method: Literal[BinarizationMethod.BRADLEY] = _method(BinarizationMethod.BRADLEY)


BinarizeChoice = Annotated[
    Annotated[OtsuParams, Tag(BinarizationMethod.OTSU)]
    | Annotated[SauvolaParams, Tag(BinarizationMethod.SAUVOLA)]
    | Annotated[WolfParams, Tag(BinarizationMethod.WOLF)]
    | Annotated[ISauvolaParams, Tag(BinarizationMethod.ISAUVOLA)]
    | Annotated[SuParams, Tag(BinarizationMethod.SU)]
    | Annotated[GatosParams, Tag(BinarizationMethod.GATOS)]
    | Annotated[NickParams, Tag(BinarizationMethod.NICK)]
    | Annotated[BradleyParams, Tag(BinarizationMethod.BRADLEY)],
    method_discriminator(DEFAULT_METHOD),
]


class BinarizeParams(RootModel[BinarizeChoice]):
    """The method of the binarization and its parameters, Sauvola when none is named."""


class InkMask:
    """Finds the ink of a gray page by a method of Doxa, and moves and rounds its edge."""

    NEIGHBOURHOOD_KEYS: ClassVar[frozenset[str]] = frozenset({WINDOW_PARAM, K_PARAM})

    def __init__(self, gray: Samples, params: CommonParams, *, window_factor: float, sigma_factor: float) -> None:
        """Take the page and what the user asked of its ink.

        :param gray: Single-plane samples of the page.
        :type gray: Samples
        :param params: The parameters of the method that was chosen.
        :type params: CommonParams
        :param window_factor: Size of the page over the size of the scan, by which the window is multiplied.
        :type window_factor: float
        :param sigma_factor: Size of the page over the size of the scan, at least 1, by which the blur is multiplied.
        :type sigma_factor: float
        """
        self._gray = np.ascontiguousarray(gray)
        self._params = params
        self._window_factor = window_factor
        self._sigma_factor = sigma_factor

    def find(self) -> NDArray[np.bool_]:
        """Find the ink.

        :returns: True where the pixel is ink.
        :rtype: NDArray[np.bool_]
        """
        method = BinarizationMethod(self._params.model_dump(mode=DUMP_MODE)[METHOD_KEY])
        options = self._params.model_dump(mode=DUMP_MODE, include=set(self.NEIGHBOURHOOD_KEYS))
        if WINDOW_PARAM in options:
            options[WINDOW_PARAM] = max(MIN_WINDOW_PX, round(options[WINDOW_PARAM] * self._window_factor))
        binarization = doxapy.Binarization(ALGORITHMS[method])
        binarization.initialize(self._gray)
        binary = np.empty_like(self._gray)
        binarization.to_binary(binary, options)
        ink = binary == BLACK
        if self._params.thickness == 0 and not self._params.smooth:
            return ink
        sigma = (SMOOTH_SIGMA_PX if self._params.smooth else SHIFT_SIGMA_PX) * self._sigma_factor
        soft = cv2.GaussianBlur(ink.astype(np.float32), (0, 0), sigma)
        cut = NEUTRAL_CUT - NEUTRAL_CUT * THICKNESS_REACH * self._params.thickness / THICKNESS_LIMIT
        return np.asarray(soft > cut, dtype=np.bool_)


class Binarize(ModelProcessor):
    """Makes a page black and white, or levels its light, by the method the user chose."""

    params_model = BinarizeParams
    spec = ProcessorSpec(
        key='cleanup.binarize',
        version='1',
        title='Binarization',
        stage=Stage.CLEANUP,
        scope=ProcessorScope.PAGE,
        outputs=frozenset({VersionOutput.IMAGE}),
        parameters=BinarizeParams.model_json_schema(),
        editor=EditorKind.REGIONS,
    )

    @override
    def run(self, step_input: StepInput) -> StepResult:
        """Make the page of the mode and by the method in the parameters.

        :param step_input: The image of the page, the parameters, and the regions edit if there is one.
        :type step_input: StepInput
        :returns: One output holding the page, with the picture zones it found in its data for a mixed page.
        :rtype: StepResult
        :raises ConflictError: If there is no image, or it cannot be read.
        """
        if step_input.image is None:
            raise ConflictError(NO_IMAGE.format(key=self.spec.key))
        params = BinarizeParams.model_validate(step_input.params).root
        scan = read_samples(step_input.image)
        facts = step_input.input_data
        enlargement = self._enlargement(params, step_input)
        enlarged = enlargement > 1.0
        page = scan
        if enlarged:
            page = np.asarray(
                cv2.resize(scan, None, fx=enlargement, fy=enlargement, interpolation=cv2.INTER_CUBIC), dtype=np.uint8
            )
        gray = np.asarray(cv2.cvtColor(page, cv2.COLOR_BGR2GRAY) if page.ndim == COLOR_PLANES else page, dtype=np.uint8)
        ink = InkMask(gray, params, window_factor=step_input.scale * enlargement, sigma_factor=enlargement).find()
        zones: list[dict[str, object]] = []
        match params.mode:
            case OutputMode.BW:
                result = self._paint(ink)
            case OutputMode.GRAY:
                result = level_light(gray)
            case OutputMode.COLOR:
                result = level_light(page)
            case OutputMode.MIXED:
                result, zones = self._mixed(page, gray, ink, step_input, enlargement)
        color_mode = self._color_mode(result)
        target = step_input.workdir / BINARIZED_IMAGE_NAME
        write_png(result, target)
        data = image_data(result, facts, color_mode) | source_size_data(scan, step_input.scale)
        data |= {VersionData.METHOD: params.method.value, VersionData.MODE: params.mode.value}
        if params.method is BinarizationMethod.OTSU:
            data[VersionData.THRESHOLD] = OtsuSplit.of(gray).threshold
        if params.mode is OutputMode.MIXED:
            data[VersionData.ZONES] = zones
        if frame := content_frame_of(facts):
            data[VersionData.CONTENT_FRAME] = frame.scaled(enlargement).to_data()
        transform = Transform()
        if enlarged:
            data[VersionData.DPI] = params.output_dpi
            transform = Transform(
                kind=TransformKind.SCALE, matrix=(enlargement, 0.0, 0.0, 0.0, enlargement, 0.0, 0.0, 0.0, 1.0)
            )
        review = settle_review(data, None, facts)
        return StepResult(
            outputs=[StepOutput(image=target, color_mode=color_mode, transform=transform, data=data, review=review)]
        )

    @staticmethod
    def _enlargement(params: CommonParams, step_input: StepInput) -> float:
        """Work out how much larger than the scan the page is to be made.

        :param params: The parameters of the step.
        :type params: CommonParams
        :param step_input: The step input, whose facts may give the resolution of the scan and whose scale tells a
                           preview from a full run.
        :type step_input: StepInput
        :returns: The factor, 1 when the page keeps the size of the scan.
        :rtype: float
        """
        size = PageSize.from_data(step_input.input_data)
        wanted = params.output_dpi != KEEP_RESOLUTION and params.mode in {OutputMode.BW, OutputMode.MIXED}
        if not wanted or size is None or size.dpi is None or step_input.scale < 1.0:
            return 1.0
        return min(MAX_ENLARGEMENT, max(1.0, params.output_dpi / size.dpi))

    @staticmethod
    def _paint(ink: NDArray[np.bool_]) -> Samples:
        """Paint the ink black on white.

        :param ink: True where the pixel is ink.
        :type ink: NDArray[np.bool_]
        :returns: A plane of black and white samples.
        :rtype: Samples
        """
        return np.where(ink, BLACK, WHITE).astype(np.uint8)

    @staticmethod
    def _mixed(
        page: Samples, gray: Samples, ink: NDArray[np.bool_], step_input: StepInput, enlargement: float
    ) -> tuple[Samples, list[dict[str, object]]]:
        """Make the text black and white and keep the pictures in their tones, with their light levelled.

        :param page: The samples of the page.
        :type page: Samples
        :param gray: The same page in one plane.
        :type gray: Samples
        :param ink: True where the pixel is ink.
        :type ink: NDArray[np.bool_]
        :param step_input: The step input, which may hold the regions edit.
        :type step_input: StepInput
        :param enlargement: Size of the page over the size of the scan.
        :type enlargement: float
        :returns: The page, and the zones the search found in the pixels of the full image the step read.
        :rtype: tuple[Samples, list[dict[str, object]]]
        """
        ratio = step_input.scale * enlargement
        edit = step_input.edit
        drawn = edit.geometry.scaled(ratio) if edit is not None and isinstance(edit.geometry, Regions) else None
        search = PictureZones(level_light(gray))
        found = search.find()
        picture = search.mask(found, drawn).astype(np.bool_)
        zones = [zone.scaled(1 / ratio).to_data() for zone in found]
        text = Binarize._paint(ink)
        if not picture.any():
            return text, zones
        tones = level_light(page)
        if tones.ndim == COLOR_PLANES:
            picture, text = picture[..., np.newaxis], text[..., np.newaxis]
        return np.where(picture, tones, text).astype(np.uint8), zones

    @staticmethod
    def _color_mode(result: Samples) -> ColorMode:
        """Tell what the page is by its samples.

        :param result: The page that was made.
        :type result: Samples
        :returns: Colour for three planes, black and white for a plane of two tones, gray else.
        :rtype: ColorMode
        """
        if result.ndim == COLOR_PLANES:
            return ColorMode.COLOR
        return ColorMode.BILEVEL if np.all((result == BLACK) | (result == WHITE)) else ColorMode.GRAY
