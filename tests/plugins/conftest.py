"""Fixtures of the tests of the processor plugins, which skip those of the OpenCV plugins where OpenCV is missing."""

from typing import TYPE_CHECKING, cast

import pytest

from bookreviver.ports.processing import Processor
from tests.helpers.samples import CV_MISSING

if TYPE_CHECKING:
    from collections.abc import Callable

    from bookreviver.plugins.gutter import GutterSearch


@pytest.fixture
def fx_deskew() -> Processor:
    """Build the deskew processor, or skip the test where OpenCV is not installed.

    :returns: The processor ``geometry.deskew``.
    :rtype: Processor
    """
    processor = pytest.importorskip('bookreviver.plugins.deskew', reason=CV_MISSING).Deskew()
    assert isinstance(processor, Processor)
    return processor


@pytest.fixture
def fx_split_spread() -> Processor:
    """Build the spread splitter, or skip the test where OpenCV is not installed.

    :returns: The processor ``split.spread``.
    :rtype: Processor
    """
    processor = pytest.importorskip('bookreviver.plugins.split_spread', reason=CV_MISSING).SplitSpread()
    assert isinstance(processor, Processor)
    return processor


@pytest.fixture
def fx_gutter_search() -> Callable[..., GutterSearch]:
    """Offer a builder of the gutter search, or skip the test where OpenCV is not installed.

    :returns: A function that builds a ``GutterSearch`` from the parameters it is given, the defaults for any it lacks.
    :rtype: Callable[..., GutterSearch]
    """
    gutter = pytest.importorskip('bookreviver.plugins.gutter', reason=CV_MISSING)

    def build(**params: float) -> GutterSearch:
        """Build a gutter search.

        :param params: Parameters of the search that differ from the defaults.
        :type params: float
        :returns: The search.
        :rtype: GutterSearch
        """
        return cast('GutterSearch', gutter.GutterSearch(gutter.GutterParams(**params)))

    return build


@pytest.fixture
def fx_split_auto() -> Processor:
    """Build the automatic splitter, or skip the test where OpenCV is not installed.

    :returns: The processor ``split.auto``.
    :rtype: Processor
    """
    processor = pytest.importorskip('bookreviver.plugins.split_auto', reason=CV_MISSING).SplitAuto()
    assert isinstance(processor, Processor)
    return processor


@pytest.fixture
def fx_perspective() -> Processor:
    """Build the perspective processor, or skip the test where OpenCV is not installed.

    :returns: The processor ``geometry.perspective``.
    :rtype: Processor
    """
    processor = pytest.importorskip('bookreviver.plugins.perspective', reason=CV_MISSING).Perspective()
    assert isinstance(processor, Processor)
    return processor


@pytest.fixture
def fx_crop() -> Processor:
    """Build the crop processor, or skip the test where OpenCV is not installed.

    :returns: The processor ``geometry.crop``.
    :rtype: Processor
    """
    processor = pytest.importorskip('bookreviver.plugins.crop', reason=CV_MISSING).Crop()
    assert isinstance(processor, Processor)
    return processor
