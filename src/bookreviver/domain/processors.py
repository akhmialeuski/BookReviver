"""What a processor says about itself: what it writes, where it runs and where it stands in a recipe."""

from typing import TYPE_CHECKING

from attrs import field, frozen, validators

from bookreviver.domain.geometry import EditorKind
from bookreviver.domain.labels import LabeledStrEnum
from bookreviver.domain.versions import ProcessorRef

if TYPE_CHECKING:
    from bookreviver.domain.ids import StepId
    from bookreviver.domain.metadata import MetadataMap
    from bookreviver.domain.stages import Stage
    from bookreviver.domain.step_measures import StepMeasure


class OrderRuleKind(LabeledStrEnum):
    """How firmly a processor asks for its place among the steps of a recipe."""

    USUAL = 'usual', 'Usual place'
    REQUIRED = 'required', 'Required place'


class ProcessorScope(LabeledStrEnum):
    """How many outputs a processor makes from its input."""

    PAGE = 'page', 'One output for the page'
    SPLIT = 'split', 'One output for each part of a scan'


class VersionOutput(LabeledStrEnum):
    """What a processing step writes."""

    IMAGE = 'image', 'Page image'
    MASK = 'mask', 'Mask'
    REGIONS = 'regions', 'Regions'
    TEXT = 'text', 'Text'


class WorkerPool(LabeledStrEnum):
    """Class of worker a job needs, which routes it to the right queue."""

    CPU = 'cpu', 'CPU'
    GPU = 'gpu', 'GPU'
    LLM = 'llm', 'Language model'


@frozen(kw_only=True)
class OrderRule:
    """The place a processor asks for among the steps of a recipe, relative to the steps of one other processor.

    :ivar processor_key: Key of the other processor.
    :ivar reason: One sentence for the reader that names both steps and says why the place matters.
    """

    processor_key: str = field(validator=validators.min_len(1))
    reason: str = field(validator=validators.min_len(1))


@frozen(kw_only=True)
class OrderIssue:
    """A step that stands where its processor does not want it, found in the steps of a recipe.

    :ivar step_id: The step that is out of place, which is the one whose processor declared the rule.
    :ivar processor_key: Key of its processor.
    :ivar kind: Whether the place is the usual one, which a user may leave, or a required one.
    :ivar other_step_id: The step the rule compares it with.
    :ivar other_key: Key of the processor of that step.
    :ivar reason: Why the place matters, which the interface shows.
    """

    step_id: StepId
    processor_key: str
    kind: OrderRuleKind
    other_step_id: StepId
    other_key: str
    reason: str


@frozen(kw_only=True)
class ProcessorSpec:
    """What a processor says about itself, which the catalogue lists without running it.

    The spec is a plain value declared on the processor's class, so the API server lists every processor without
    importing what a processor needs to run.

    :ivar key: Key of the processor, ``<stage>.<name>`` or ``split.<name>``, such as ``geometry.deskew``.
    :ivar version: Version of the algorithm, which joins the identifier of the page versions it makes.
    :ivar title: Name the interface shows.
    :ivar summary: One line the catalogue of steps shows under the title, saying what the step does to a page.
    :ivar stage: Stage whose recipe the step may be put into.
    :ivar scope: Whether the step makes one output for the page or one for each part of a scan.
    :ivar outputs: What the step writes.
    :ivar parameters: JSON Schema of the parameters, from which the interface builds the settings form.
    :ivar editor: Editor of the manual edit the step reads.
    :ivar pool: Class of worker the step runs on.
    :ivar by_page_side: Whether the step reads the side of the book its page lies on, so a page that moves to the other
                        side is made again.
    :ivar after: Processors whose steps this step usually stands after. A step that stands before one of them is
                 marked, and may stay.
    :ivar before: Processors whose steps this step usually stands before, marked the same way.
    :ivar requires_after: Processors whose steps this step must stand after, since it works on what they leave. A step
                          that stands before one of them is refused, unless the recipe is saved in the free order.
    :ivar measure: What the step finds on each page that the strip compares with the rest of the book, so a page that
                   departs from it is listed, or None for a step that finds nothing to compare.
    """

    key: str = field(validator=validators.min_len(1))
    version: str = field(validator=validators.min_len(1))
    title: str = field(validator=validators.min_len(1))
    summary: str = ''
    stage: Stage
    scope: ProcessorScope = ProcessorScope.PAGE
    outputs: frozenset[VersionOutput] = frozenset({VersionOutput.IMAGE})
    parameters: MetadataMap = field(factory=dict)
    editor: EditorKind = EditorKind.NONE
    pool: WorkerPool = WorkerPool.CPU
    by_page_side: bool = False
    after: tuple[OrderRule, ...] = ()
    before: tuple[OrderRule, ...] = ()
    requires_after: tuple[OrderRule, ...] = ()
    measure: StepMeasure | None = None

    @property
    def ref(self) -> ProcessorRef:
        """The key and the version of the processor, as a page version records them."""
        return ProcessorRef(key=self.key, version=self.version)
