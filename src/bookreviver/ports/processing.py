"""Processing ports: what a step of a plugin reads and makes, and the executor that runs the step off the event loop.

A plugin is one step of a recipe, such as deskewing a page, and its contract is ``bookreviver.core.plugins``. The half
of it that computes is synchronous and touches only the local paths of its ``StepInput``, since the libraries it uses,
OpenCV and libvips, block. The step executor port decides where it runs, in a thread or in a pool of processes, so the
work stays off the event loop of the application. The service hands the executor the paths the asset store gives, and
stores what the processor writes.

A processor writes its image without loss, in a PNG, or leaves an image the service may read as it is, such as the file
the page was already stored in. The format a version stores is chosen later by the service from the book's image
policy, so a processor knows nothing of that setting. It never deletes a file it was not given, and the work directory
it writes into is removed after its files are stored.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from attrs import field, frozen

from bookreviver.domain.geometry import Transform

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from bookreviver.domain.images import ColorMode
    from bookreviver.domain.metadata import MetadataMap
    from bookreviver.domain.page_steps import PageEdit
    from bookreviver.domain.sides import PageSide
    from bookreviver.domain.stages import ReviewReason
    from bookreviver.domain.versions import VersionScale


@frozen(kw_only=True)
class StepInput:
    """What a processor reads to run one step on one page.

    :ivar image: ``full`` or ``preview`` image of the input version, the ``full`` image of the scan for a step that
                 splits a scan, or None for a step that reads no image, such as the one that makes a blank leaf.
    :ivar scale: 1 for a full run, and the ratio of the preview to the full image for a preview.
    :ivar params: Parameters of the step after ``validate_params``.
    :ivar edit: Manual edit of the step, or None.
    :ivar edit_mask: Path of the mask of the edit, or None.
    :ivar input_data: ``data`` of the input version, or the facts of the scan for a step that splits one.
    :ivar side: Side of the book the page lies on, given only to a step whose spec says ``by_page_side``.
    :ivar references: ``full`` images of other pages that the step looks at without processing them, such as the
                      neighbours a blank leaf takes the colour of its paper from, or none.
    :ivar workdir: Empty directory the step writes its output files into.
    """

    image: Path | None
    scale: float = 1.0
    params: MetadataMap = field(factory=dict)
    edit: PageEdit | None = None
    edit_mask: Path | None = None
    input_data: MetadataMap = field(factory=dict)
    side: PageSide | None = None
    references: Sequence[Path] = ()
    workdir: Path


@frozen(kw_only=True)
class StepOutput:
    """What a processor made of one part of its input.

    :ivar image: Image the step made, in ``workdir`` or the unchanged input, or None for a step without an image.
    :ivar color_mode: Whether the image is bilevel, gray or colour, which decides the format it is stored in.
    :ivar transform: Transform of coordinates from the input image to this one.
    :ivar data: Data of the step, such as an angle, a confidence, or the size of the image.
    :ivar review: Why the step wants the page looked at again though it finished, or None when it is sure. The service
                  stores it with the version as it is and reads nothing of ``data`` to find it.
    :ivar mask: Mask of the areas the step removed, in ``workdir``, or None.
    :ivar mesh: File in ``workdir`` holding the mesh the step followed, or None. The runner stores it with the version
                and gives the transform the key it is stored under, so the step leaves the transform of a mesh to it.
    """

    image: Path | None = None
    color_mode: ColorMode
    transform: Transform = field(factory=Transform)
    data: MetadataMap = field(factory=dict)
    review: ReviewReason | None = None
    mask: Path | None = None
    mesh: Path | None = None


@frozen(kw_only=True)
class StepResult:
    """What a processor returns: one output, or one for each part of a scan for a step that splits it.

    :ivar outputs: The outputs, in the order of the parts of the input, such as the left half and then the right half.
    """

    outputs: Sequence[StepOutput] = field(factory=tuple)


@frozen(kw_only=True)
class ProcessorSettings:
    """What the application tells a processor about the machine it runs on.

    :ivar models_dir: Directory the files of neural models are kept in, into which a processor downloads a model the
                      first time it needs it.
    """

    models_dir: Path


class StepExecutor(ABC):
    """Runs one step of a processor of the catalogue in a place the application chooses, off the event loop."""

    @abstractmethod
    async def run(self, *, processor_key: str, scale: VersionScale, step_input: StepInput) -> StepResult:
        """Run a processor on an input: ``Processor.run`` for a full run and ``Processor.preview`` for a preview.

        :param processor_key: Key of the processor, such as ``geometry.deskew``.
        :type processor_key: str
        :param scale: Whether the step runs on the full image or on the preview.
        :type scale: VersionScale
        :param step_input: What the step reads, with the directory it writes into.
        :type step_input: StepInput
        :returns: The outputs of the step.
        :rtype: StepResult
        :raises NotFoundError: If no processor has this key.
        :raises DomainError: If the processor cannot run on this input, with the type and the message it raised.
        """
