"""Fixtures of the tests of the processor plugins, which skip those of the OpenCV plugins where OpenCV is missing."""

import pytest

from bookreviver.ports.processing import Processor
from tests.helpers.samples import CV_MISSING


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
