"""Fixtures of the tests of the processor plugins, one for each built-in processor that needs OpenCV."""

from typing import TYPE_CHECKING

import pytest

from bookreviver.plugins.cleanup.binarize import Binarize
from bookreviver.plugins.cleanup.despeckle import Despeckle
from bookreviver.plugins.cleanup.eraser import Eraser
from bookreviver.plugins.cleanup.thickness import Thickness
from bookreviver.plugins.geometry.crop import Crop
from bookreviver.plugins.geometry.deskew import Deskew
from bookreviver.plugins.geometry.dewarp import Dewarp
from bookreviver.plugins.geometry.normalize import Normalize
from bookreviver.plugins.geometry.perspective import Perspective
from bookreviver.plugins.pages.content_type import ContentTypeProbe
from bookreviver.plugins.split.gutter import GutterParams, GutterSearch
from bookreviver.plugins.split.split_auto import SplitAuto
from bookreviver.plugins.split.split_spread import SplitSpread

if TYPE_CHECKING:
    from collections.abc import Callable

    from bookreviver.core.plugins import Processor


@pytest.fixture
def fx_deskew() -> Processor:
    """Build the deskew processor.

    :returns: The processor ``geometry.deskew``.
    :rtype: Processor
    """
    return Deskew()


@pytest.fixture
def fx_split_spread() -> Processor:
    """Build the spread splitter.

    :returns: The processor ``split.spread``.
    :rtype: Processor
    """
    return SplitSpread()


@pytest.fixture
def fx_gutter_search() -> Callable[..., GutterSearch]:
    """Offer a builder of the gutter search.

    :returns: A function that builds a ``GutterSearch`` from the parameters it is given, the defaults for any it lacks.
    :rtype: Callable[..., GutterSearch]
    """

    def build(**params: float) -> GutterSearch:
        """Build a gutter search.

        :param params: Parameters of the search that differ from the defaults.
        :type params: float
        :returns: The search.
        :rtype: GutterSearch
        """
        return GutterSearch(GutterParams(**params))

    return build


@pytest.fixture
def fx_split_auto() -> Processor:
    """Build the automatic splitter.

    :returns: The processor ``split.auto``.
    :rtype: Processor
    """
    return SplitAuto()


@pytest.fixture
def fx_perspective() -> Processor:
    """Build the perspective processor.

    :returns: The processor ``geometry.perspective``.
    :rtype: Processor
    """
    return Perspective()


@pytest.fixture
def fx_dewarp() -> Processor:
    """Build the dewarping processor.

    :returns: The processor ``geometry.dewarp``, which is not given a models directory.
    :rtype: Processor
    """
    return Dewarp()


@pytest.fixture
def fx_crop() -> Processor:
    """Build the crop processor.

    :returns: The processor ``geometry.crop``.
    :rtype: Processor
    """
    return Crop()


@pytest.fixture
def fx_normalize() -> Processor:
    """Build the normalize processor.

    :returns: The processor ``geometry.normalize``.
    :rtype: Processor
    """
    return Normalize()


@pytest.fixture
def fx_binarize() -> Processor:
    """Build the binarize processor.

    :returns: The processor ``cleanup.binarize``.
    :rtype: Processor
    """
    return Binarize()


@pytest.fixture
def fx_despeckle() -> Processor:
    """Build the despeckle processor.

    :returns: The processor ``cleanup.despeckle``.
    :rtype: Processor
    """
    return Despeckle()


@pytest.fixture
def fx_thickness() -> Processor:
    """Build the thickness processor.

    :returns: The processor ``cleanup.thickness``.
    :rtype: Processor
    """
    return Thickness()


@pytest.fixture
def fx_eraser() -> Processor:
    """Build the eraser processor.

    :returns: The processor ``cleanup.eraser``.
    :rtype: Processor
    """
    return Eraser()


@pytest.fixture
def fx_content_type() -> Processor:
    """Build the processor that tells what a page shows.

    :returns: The processor ``pages.content``.
    :rtype: Processor
    """
    return ContentTypeProbe()
