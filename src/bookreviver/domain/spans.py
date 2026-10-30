"""The names and the facts of the spans the application records when its measurements are switched on."""

from bookreviver.domain.labels import LabeledStrEnum


class SpanName(LabeledStrEnum):
    """A kind of span the application records when its measurements are switched on, named by what it times."""

    HTTP_REQUEST = 'http.request', 'HTTP request'
    JOB = 'job', 'Background job'
    LOOP_LAG = 'loop.lag', 'One tick of the event-loop lag probe'
    RUN_PAGE = 'run.page', 'Run of a stage on one page'
    STEP = 'step', 'One step of a recipe on one page'
    WRITE_BLOCK = 'write.block', 'Block that holds the write lock of the database'


class SpanAttribute(LabeledStrEnum):
    """A key of the facts a span carries, which the report of a run groups and sums by."""

    ROUTE = 'route', 'Route template of the request'
    METHOD = 'method', 'HTTP method'
    STATUS = 'status', 'HTTP status of the answer'
    JOB_KIND = 'job.kind', 'Kind of the job'
    JOB_ID = 'job.id', 'Identifier of the job'
    BOOK = 'book', 'Name of the directory of the book the block holds'
    PAGE = 'page', 'Identifier of the page'
    STAGE = 'stage', 'Stage of the run'
    RUN_OUTCOME = 'run.outcome', 'What the run came to on the page'
    STEP_TYPE = 'step.type', 'Key of the processor of the step'
    STEP_SCALE = 'step.scale', 'Whether the step ran on the full image or on the preview'
    STEP_OUTCOME = 'step.outcome', 'Whether the step was found made, run, or failed'
    BLOCK_KIND = 'block.kind', 'Whether the block holds a book or writes outside of one'
    BLOCK_OUTCOME = 'block.outcome', 'How the block ended'
    WAIT_MS = (
        'wait.ms',
        'Milliseconds from asking for the writer until the block had it, event-loop delay included',
    )
    HOLD_MS = 'hold.ms', 'Milliseconds the work held the transaction, from BEGIN until its commit began'
    COMMIT_MS = 'commit.ms', 'Milliseconds the commit of the block took'
    LAG_MS = 'lag.ms', 'Milliseconds a sleep of the probe overran its interval'


class SpanParent(LabeledStrEnum):
    """Which span a new span is the child of, which work of its own, such as a job, does not leave to the context."""

    CURRENT = 'current', 'The span current where it is opened'
    NONE = 'none', 'No parent: the span starts a trace of its own'


class StepOutcome(LabeledStrEnum):
    """How a step of a recipe on one page was settled, which says whether its processor ran."""

    CACHED = 'cached', 'Found made by an earlier run'
    RAN = 'ran', 'Run by the processor'
    FAILED = 'failed', 'Failed or refused'


class BlockKind(LabeledStrEnum):
    """What a block that holds the write lock of the database changes."""

    BOOK = 'book', 'The content of one book'
    OTHER = 'other', 'Something outside the content of a book'


class BlockOutcome(LabeledStrEnum):
    """How a block that held the write lock of the database ended."""

    COMMITTED = 'committed', 'Committed'
    ROLLED_BACK = 'rolled-back', 'Rolled back'
    NOT_BEGUN = 'not-begun', 'The transaction did not begin'
