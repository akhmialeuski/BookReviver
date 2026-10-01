"""Processing ports: the contract of a processor plugin and the catalogue that finds the plugins.

A processor is one step of a recipe, such as deskewing a page. It is a plugin: the built-in ones live in ``plugins/``
and are registered through the entry point group ``bookreviver.processors`` that an external package also uses, so the
application cannot tell them apart. The processor itself is synchronous and touches only the local paths of its
``StepInput``, since the libraries it uses, OpenCV and libvips, block. The service runs it in a worker thread, hands it
the paths the asset store gives, and stores what it writes.

A processor writes its image without loss, in a PNG, or leaves an image the service may read as it is, such as the file
the page was already stored in. The format a version stores is chosen later by the service from the project's image
policy, so a processor knows nothing of that setting. It never deletes a file it was not given, and the work directory
it writes into is removed after its files are stored.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

from attrs import field, frozen

from bookreviver.domain.geometry import Transform

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from bookreviver.domain.entities import PageEdit
    from bookreviver.domain.enums import ColorMode
    from bookreviver.domain.values import MetadataMap, ProcessorSpec


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
    :ivar workdir: Empty directory the step writes its output files into.
    """

    image: Path | None
    scale: float = 1.0
    params: MetadataMap = field(factory=dict)
    edit: PageEdit | None = None
    edit_mask: Path | None = None
    input_data: MetadataMap = field(factory=dict)
    workdir: Path


@frozen(kw_only=True)
class StepOutput:
    """What a processor made of one part of its input.

    :ivar image: Image the step made, in ``workdir`` or the unchanged input, or None for a step without an image.
    :ivar color_mode: Whether the image is bilevel, gray or colour, which decides the format it is stored in.
    :ivar transform: Transform of coordinates from the input image to this one.
    :ivar data: Data of the step, such as an angle, a confidence, or the size of the image.
    :ivar mask: Mask of the areas the step removed, in ``workdir``, or None.
    """

    image: Path | None = None
    color_mode: ColorMode
    transform: Transform = field(factory=Transform)
    data: MetadataMap = field(factory=dict)
    mask: Path | None = None


@frozen(kw_only=True)
class StepResult:
    """What a processor returns: one output, or one for each part of a scan for a step that splits it.

    :ivar outputs: The outputs, in the order of the parts of the input, such as the left half and then the right half.
    """

    outputs: Sequence[StepOutput] = field(factory=tuple)


class Processor(ABC):
    """One step of a recipe, registered as a plugin.

    :ivar spec: What the processor says about itself, declared on its class so the catalogue lists it without running
                it.
    """

    spec: ClassVar[ProcessorSpec]

    @abstractmethod
    def validate_params(self, raw: MetadataMap) -> MetadataMap:
        """Check the parameters of a step against the schema of the processor and fill in the defaults.

        :param raw: Parameters as a recipe or a form gives them, possibly missing the ones with defaults.
        :type raw: MetadataMap
        :returns: The parameters with every default filled in, which are what the identifier of a version hashes.
        :rtype: MetadataMap
        :raises InvalidParametersError: If a parameter is missing, unknown or out of its range.
        """

    @abstractmethod
    def run(self, step_input: StepInput) -> StepResult:
        """Run the step on the full image.

        :param step_input: What the step reads, with the directory it writes into.
        :type step_input: StepInput
        :returns: The outputs of the step.
        :rtype: StepResult
        :raises DomainError: If the step cannot run on this input, which fails its version with the message.
        """

    def preview(self, step_input: StepInput) -> StepResult:
        """Run the step on the downscaled image of a preview, which a processor may do faster than a full run.

        :param step_input: What the step reads, whose image is the preview of the input version.
        :type step_input: StepInput
        :returns: The outputs of the step, which by default are those of a full run.
        :rtype: StepResult
        :raises DomainError: If the step cannot run on this input.
        """
        return self.run(step_input)


class ProcessorCatalog(ABC):
    """The processors the application can run, found by their key."""

    @abstractmethod
    def get(self, key: str) -> Processor:
        """Return the processor with this key.

        :param key: Key of the processor, such as ``geometry.deskew``.
        :type key: str
        :returns: The processor.
        :rtype: Processor
        :raises NotFoundError: If no processor has this key.
        """

    @abstractmethod
    def specs(self) -> Sequence[ProcessorSpec]:
        """List what every processor says about itself, by key.

        :returns: The specs of all the processors of the catalogue.
        :rtype: Sequence[ProcessorSpec]
        """
