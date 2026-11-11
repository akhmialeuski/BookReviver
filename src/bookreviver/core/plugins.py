"""The contract of a plugin, which is one step of a recipe, and the catalogue that finds the plugins.

A plugin is a step, such as deskewing a page. The built-in ones live in ``plugins/`` and are registered through the
entry point group ``bookreviver.processors`` that an external package also uses, so the application cannot tell them
apart. A plugin has two halves, because the heavy work runs in another process, which a book with its database cannot
be handed to.

``Processor`` is the half that computes. It is synchronous, reads the local paths of its ``StepInput`` and writes
files, and the step executor runs it where the application chooses, in a thread or in a pool of processes. It writes
its image without loss, in a PNG, or leaves an image the service may read as it is, such as the file the page was
already stored in. The format a version stores is chosen later by the service from the book's image policy, so a
processor knows nothing of that setting. It never deletes a file it was not given, and the work directory it writes
into is removed after its files are stored.

``BookStep`` is the half that reads the book. It runs in the process of the application, inside a transaction of the
book, and is given the ``BookContent`` of that transaction: the pages of the book and their versions. A step whose
result on one page depends on the other pages, such as the one that brings every page to one size, declares it, and
the engine asks it what the book gives before it runs the step on a page. A step that needs one page only has none.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from bookreviver.core.content import BookContent
    from bookreviver.domain.ids import PageId
    from bookreviver.domain.metadata import MetadataMap
    from bookreviver.domain.processors import ProcessorSpec
    from bookreviver.domain.stages import Stage
    from bookreviver.domain.versions import PageVersion
    from bookreviver.ports.processing import ProcessorSettings, StepInput, StepResult


class BookStep(ABC):
    """The half of a plugin that reads the whole book, in the process of the application.

    Some parameters of such a step may be left to the book, which is to say that the step takes them from what it
    found on the other pages. The engine lays what ``from_book`` returns over the parameters of the step before the
    step runs on a page, so the version records the parameters it ran with. Every method is synchronous and only reads,
    through the content of the transaction its caller runs in.
    """

    @abstractmethod
    def leaves_to_book(self, params: MetadataMap) -> bool:
        """Tell whether the parameters leave a field to the book, so the result of a page depends on the other pages.

        :param params: Parameters of the step, as a recipe stores them or with the defaults filled in.
        :type params: MetadataMap
        :returns: Whether a field is left to the book.
        :rtype: bool
        """

    @abstractmethod
    def from_book(
        self, content: BookContent, stage: Stage, params: MetadataMap, fresh: Mapping[PageId, PageVersion]
    ) -> MetadataMap:
        """Work out the fields the parameters leave to the book, from what the step made on the pages of the book.

        :param content: The book as the transaction of the caller sees it.
        :type content: BookContent
        :param stage: Stage of the step.
        :type stage: Stage
        :param params: Parameters of the step with the defaults filled in.
        :type params: MetadataMap
        :param fresh: The versions of the step that a run has just made, by page, which stand in place of those the
                      pages hold as current.
        :type fresh: Mapping[PageId, PageVersion]
        :returns: The fields to lay over the parameters, none when nothing is left to the book or no page has a result.
        :rtype: MetadataMap
        """

    @abstractmethod
    def measured(self, content: BookContent, stage: Stage, steps: Sequence[MetadataMap]) -> Sequence[MetadataMap]:
        """Work out what a measure of the book writes into the steps of the plugin in the recipes of a stage.

        :param content: The book as the transaction of the caller sees it.
        :type content: BookContent
        :param stage: Stage of the steps.
        :type stage: Stage
        :param steps: Parameters of the step of the plugin in each recipe of the stage that has one.
        :type steps: Sequence[MetadataMap]
        :returns: The fields to write over the parameters of each of the steps, in the same order.
        :rtype: Sequence[MetadataMap]
        :raises ConflictError: If the step has no result on any page yet, so there is nothing to measure.
        """

    @abstractmethod
    def page_size(self, params: MetadataMap) -> tuple[int, int] | None:
        """Tell the size the step brings every page to with these parameters.

        :param params: Parameters of the step as a recipe stores them.
        :type params: MetadataMap
        :returns: The width and the height in pixels, or None while the parameters leave the size to the book.
        :rtype: tuple[int, int] | None
        """


class Processor(ABC):
    """One step of a recipe, registered as a plugin.

    :ivar spec: What the processor says about itself, declared on its class so the catalogue lists it without running
                it.
    :ivar settings: What the application told the processor about the machine, or None while it has told nothing.
    :ivar book_step: The half of the plugin that reads the whole book, or None for a step that needs one page only.
    """

    spec: ClassVar[ProcessorSpec]
    settings: ProcessorSettings | None = None
    book_step: ClassVar[BookStep | None] = None

    def configure(self, settings: ProcessorSettings) -> None:
        """Take the settings of the application, which the catalogue gives every processor it loads.

        A processor that needs something of them reads ``settings`` or overrides this to prepare it.

        :param settings: What the application tells the processor about the machine.
        :type settings: ProcessorSettings
        """
        self.settings = settings

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
