"""Running one step of a geometry processor on an image, for the tests that check what the step made of it."""

from typing import TYPE_CHECKING, NotRequired, TypedDict, Unpack
from uuid import uuid4

from bookreviver.domain.ids import PageId
from bookreviver.ports.processing import StepInput
from tests.helpers.builders import make_geometry_edit

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.domain.geometry import EditGeometry
    from bookreviver.domain.values import MetadataMap
    from bookreviver.ports.processing import Processor, StepOutput


class StepExtras(TypedDict):
    """What a test may give a step besides its image and its parameters.

    :ivar edit: The shape of the manual edit of the page.
    :ivar facts: Data of the input version, such as the sides the scanner cut.
    :ivar scale: Ratio of the image to the full image, 1 for a full run.
    """

    edit: NotRequired[EditGeometry]
    facts: NotRequired[MetadataMap]
    scale: NotRequired[float]


def run_on(
    processor: Processor, image: Path, workdir: Path, params: MetadataMap | None = None, **extras: Unpack[StepExtras]
) -> StepOutput:
    """Run the step on an image and return its one output.

    :param processor: The processor under test.
    :type processor: Processor
    :param image: Image to read.
    :type image: Path
    :param workdir: Directory the step writes into.
    :type workdir: Path
    :param params: Parameters of the step, the defaults where none are given.
    :type params: MetadataMap | None
    :param extras: The edit, the facts of the input and the scale of the image, each when the test gives it.
    :type extras: Unpack[StepExtras]
    :returns: The output.
    :rtype: StepOutput
    """
    shape = extras.get('edit')
    edit = (
        None
        if shape is None
        else make_geometry_edit(page_id=PageId(uuid4()), processor_key=processor.spec.key, geometry=shape)
    )
    step_input = StepInput(
        image=image,
        params=processor.validate_params(params or {}),
        edit=edit,
        scale=extras.get('scale', 1.0),
        input_data=extras.get('facts', {}),
        workdir=workdir,
    )
    [output] = processor.run(step_input).outputs
    return output
