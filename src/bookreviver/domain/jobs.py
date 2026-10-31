"""Background jobs: their kinds, their state and progress, and the parameters each kind runs with."""

from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar, Self
from uuid import UUID

from attrs import field, frozen, validators

from bookreviver.domain.errors import InvalidParametersError
from bookreviver.domain.ids import PageId, PageVersionId
from bookreviver.domain.labels import LabeledStrEnum
from bookreviver.domain.recipes import Step
from bookreviver.domain.runs import RunMode
from bookreviver.domain.stages import Stage

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.ids import JobId
    from bookreviver.domain.metadata import MetadataMap
    from bookreviver.domain.sources import SourceFile


class JobKind(LabeledStrEnum):
    """What a background job does."""

    IMPORT_SOURCE = 'import-source', 'Import source'
    PREPARE_PAGES = 'prepare-pages', 'Prepare pages'
    RUN_STAGE = 'run-stage', 'Run a stage'
    PREVIEW_STEP = 'preview-step', 'Preview a step'
    CUT_TILES = 'cut-tiles', 'Cut tiles'
    COLLECT_VERSIONS = 'collect-versions', 'Collect old versions'
    MEASURE_BOOK = 'measure-book', 'Measure the book'
    DETECT_CONTENT = 'detect-content', 'Detect the content of pages'

    @classmethod
    def processing(cls) -> frozenset[JobKind]:
        """Return the kinds of job that read and write the versions of pages, of which a book runs one at a time.

        A run, a preview and a tile cutting write the files of versions they may have found made already, and a
        collection deletes them, so no two of them may overlap, and two of one kind would write the same files. A
        measure of the book reads the versions a run writes and rewrites the parameters a run reads, so it is one of
        them.

        :returns: The kinds of the processing jobs.
        :rtype: frozenset[JobKind]
        """
        return cls.requested() | cls.housekeeping()

    @classmethod
    def requested(cls) -> frozenset[JobKind]:
        """Return the kinds of processing job that the user asks for, of which a book has one queued or running.

        :returns: A run, a preview and a measure of the book.
        :rtype: frozenset[JobKind]
        """
        return frozenset({cls.RUN_STAGE, cls.PREVIEW_STEP, cls.MEASURE_BOOK})

    @classmethod
    def preemptive(cls) -> frozenset[JobKind]:
        """Return the kinds of job that cancel a preview of the book instead of being refused by it.

        A preview is a look at one page that the editor of a step asks for by itself and the reader asks for with a
        switch, and it changes nothing that is current: it writes versions of the preview scale only, which no run and
        no measure reads, under identifiers that name that scale. A run and a measure of the book are what the reader
        pressed a button for, so a preview that happens to be in the way gives the book up, queued or running.

        :returns: A run and a measure of the book.
        :rtype: frozenset[JobKind]
        """
        return frozenset({cls.RUN_STAGE, cls.MEASURE_BOOK})

    @classmethod
    def housekeeping(cls) -> frozenset[JobKind]:
        """Return the kinds of processing job that the application queues for itself, of which a book has one.

        A job of these kinds never refuses what the user asks for. The request is stored and waits, and starts when
        the housekeeping job ends.

        :returns: A tile cutting and a collection of old versions.
        :rtype: frozenset[JobKind]
        """
        return frozenset({cls.CUT_TILES, cls.COLLECT_VERSIONS})


class JobState(LabeledStrEnum):
    """Lifecycle of a background job."""

    QUEUED = 'queued', 'Queued'
    RUNNING = 'running', 'Running'
    SUCCEEDED = 'succeeded', 'Succeeded'
    FAILED = 'failed', 'Failed'
    CANCELLED = 'cancelled', 'Cancelled'

    @property
    def is_final(self) -> bool:
        """Whether the job will not change state any more."""
        return self in {JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED}

    @classmethod
    def active(cls) -> frozenset[JobState]:
        """Return the states of a job that has not finished, which a worker or a cancellation may still leave.

        :returns: Every state that is not final.
        :rtype: frozenset[JobState]
        """
        return frozenset(state for state in cls if not state.is_final)


# Keys in the stored parameters of the jobs: the last step of a ``run-stage`` job to run, and the pages a job works on
THROUGH_STEP_KEY: str = 'through_step'
PAGE_IDS_KEY: str = 'page_ids'
# Keys of the mode and of its confirmation in the stored parameters of a ``run-stage`` job
MODE_KEY: str = 'mode'
CONFIRM_OVERWRITE_KEY: str = 'confirm_overwrite'


@frozen(kw_only=True)
class ImportRequest:
    """The files an import job was asked to import, as they were staged.

    :ivar files: Name, size and SHA-256 digest of every staged file, in upload order.
    """

    files: Sequence[SourceFile]


@frozen(kw_only=True)
class JobEnd:
    """How a job came to its end when its tasks ran to the last one, which an entry point returns to the board.

    A cancellation is no end of this kind: it unwinds as ``JobCancelledError``, so the board records it itself.

    :cvar UNEXPECTED_FAILURE: The reason a job gives when it stopped on an error nobody expected, which was logged.
    :ivar state: The final state, succeeded or failed.
    :ivar error: Why the job failed, shown to the user, or empty for a job that succeeded.
    """

    UNEXPECTED_FAILURE: ClassVar[str] = 'The job stopped because of an unexpected error. It has been logged.'

    state: JobState = field(validator=validators.in_((JobState.SUCCEEDED, JobState.FAILED)))
    error: str = ''

    @classmethod
    def unexpected(cls) -> Self:
        """Build the end of a job that stopped on an error nobody expected, after the error was logged.

        :returns: A failed end with the standard reason.
        :rtype: Self
        """
        return cls(state=JobState.FAILED, error=cls.UNEXPECTED_FAILURE)


@frozen(kw_only=True)
class Progress:
    """How far a job has come.

    :ivar done: Steps completed.
    :ivar total: Steps in all, zero while unknown.
    """

    done: int = field(default=0, validator=validators.ge(0))
    total: int = field(default=0, validator=validators.ge(0))

    @property
    def fraction(self) -> float:
        """The completed share between 0 and 1, zero while the total is unknown."""
        return self.done / self.total if self.total else 0.0


def _params_error(kind: JobKind, error: Exception) -> InvalidParametersError:
    """Build the error of the parameters of a job that its value class cannot read.

    :param kind: Kind of the job whose parameters were read.
    :type kind: JobKind
    :param error: The error the reading raised.
    :type error: Exception
    :returns: The error to raise.
    :rtype: InvalidParametersError
    """
    err_msg = f'The parameters of a {kind} job are not valid: {error}.'
    return InvalidParametersError(err_msg)


@frozen(kw_only=True)
class StageRun:
    """What a ``run-stage`` job runs: a stage over some pages by a recipe.

    :ivar stage: Stage to run, each page by the recipe of its kind.
    :ivar page_ids: Pages to run it on, or None for every page with an image.
    :ivar confirm_unsplit: Whether the user confirmed that a page split that is undone deletes the right half of a
                           spread, which a run that would do so refuses without it.
    :ivar through_step: Index in the recipe of each page of the last step to run, or None to run through the last step
                        that is on. The steps before it are found in the cache of versions when their inputs did not
                        change.
    :ivar mode: What the run does with the settings the pages changed for its steps and with the manual edits they read:
                keeps both, which is the usual run, or takes one of them away from every page it goes over first.
    :ivar confirm_overwrite: Whether the user confirmed that a mode that takes work away does so on the pages it
                             affects, which a request for such a run refuses without.
    """

    stage: Stage
    page_ids: tuple[PageId, ...] | None = None
    confirm_unsplit: bool = False
    through_step: int | None = field(default=None, validator=validators.optional(validators.ge(0)))
    mode: RunMode = RunMode.KEEP
    confirm_overwrite: bool = False

    def to_map(self) -> dict[str, Any]:
        """Return the value as the JSON object a job stores.

        :returns: The stage, the pages as text, the confirmations, the last step and the mode.
        :rtype: dict[str, Any]
        """
        return {
            'stage': self.stage.value,
            PAGE_IDS_KEY: None if self.page_ids is None else [str(page_id) for page_id in self.page_ids],
            'confirm_unsplit': self.confirm_unsplit,
            THROUGH_STEP_KEY: self.through_step,
            MODE_KEY: self.mode.value,
            CONFIRM_OVERWRITE_KEY: self.confirm_overwrite,
        }

    @classmethod
    def from_map(cls, stored: MetadataMap) -> Self:
        """Read the value from the JSON object ``to_map`` wrote.

        :param stored: The stored object.
        :type stored: MetadataMap
        :returns: The value.
        :rtype: Self
        :raises InvalidParametersError: If the object is not one of a ``run-stage`` job.
        """
        try:
            page_ids = stored[PAGE_IDS_KEY]
            return cls(
                stage=Stage(stored['stage']),
                page_ids=None if page_ids is None else tuple(PageId(UUID(page_id)) for page_id in page_ids),
                confirm_unsplit=bool(stored.get('confirm_unsplit', False)),
                through_step=stored.get(THROUGH_STEP_KEY),
                mode=RunMode(stored.get(MODE_KEY, RunMode.KEEP)),
                confirm_overwrite=bool(stored.get(CONFIRM_OVERWRITE_KEY, False)),
            )
        except (KeyError, ValueError, TypeError) as error:
            raise _params_error(JobKind.RUN_STAGE, error) from error


@frozen(kw_only=True)
class StepPreview:
    """What a ``preview-step`` job previews: the steps of a form on one page, up to one of them.

    :ivar page_id: Page to preview on.
    :ivar stage: Stage of the steps.
    :ivar steps: The steps as the form has them, with parameters not yet saved in a recipe.
    :ivar step_index: Index of the last step whose result is wanted; the steps before it run first.
    """

    page_id: PageId
    stage: Stage
    steps: tuple[Step, ...] = field(validator=validators.min_len(1))
    step_index: int = field(validator=validators.ge(0))

    def __attrs_post_init__(self) -> None:
        """Check that the index names one of the steps.

        :raises ValueError: If the index is past the last step.
        """
        if self.step_index >= len(self.steps):
            err_msg = f'The step index {self.step_index} is past the {len(self.steps)} steps of the preview.'
            raise ValueError(err_msg)

    def to_map(self) -> dict[str, Any]:
        """Return the value as the JSON object a job stores.

        :returns: The page, the stage, the steps and the index.
        :rtype: dict[str, Any]
        """
        return {
            'page_id': str(self.page_id),
            'stage': self.stage.value,
            'steps': [step.to_map() for step in self.steps],
            'step_index': self.step_index,
        }

    @classmethod
    def from_map(cls, stored: MetadataMap) -> Self:
        """Read the value from the JSON object ``to_map`` wrote.

        :param stored: The stored object.
        :type stored: MetadataMap
        :returns: The value.
        :rtype: Self
        :raises InvalidParametersError: If the object is not one of a ``preview-step`` job.
        """
        try:
            return cls(
                page_id=PageId(UUID(stored['page_id'])),
                stage=Stage(stored['stage']),
                steps=tuple(Step.from_map(step) for step in stored['steps']),
                step_index=stored['step_index'],
            )
        except (KeyError, ValueError, TypeError) as error:
            raise _params_error(JobKind.PREVIEW_STEP, error) from error


@frozen(kw_only=True)
class TileCut:
    """What a ``cut-tiles`` job cuts: the tile pyramids of some versions.

    :ivar version_ids: Versions to cut.
    """

    version_ids: tuple[PageVersionId, ...] = field(validator=validators.min_len(1))

    def to_map(self) -> dict[str, Any]:
        """Return the value as the JSON object a job stores.

        :returns: The identifiers of the versions.
        :rtype: dict[str, Any]
        """
        return {'version_ids': list(self.version_ids)}

    @classmethod
    def from_map(cls, stored: MetadataMap) -> Self:
        """Read the value from the JSON object ``to_map`` wrote.

        :param stored: The stored object.
        :type stored: MetadataMap
        :returns: The value.
        :rtype: Self
        :raises InvalidParametersError: If the object is not one of a ``cut-tiles`` job.
        """
        try:
            return cls(version_ids=tuple(PageVersionId(version_id) for version_id in stored['version_ids']))
        except (KeyError, ValueError, TypeError) as error:
            raise _params_error(JobKind.CUT_TILES, error) from error


@frozen(kw_only=True)
class ContentDetection:
    """What a ``detect-content`` job detects: the content type of some pages, or of those that have none yet.

    :ivar page_ids: Pages to detect, which includes the pages the user set by hand and the pages of every kind, since
                    the user asked for them by name, or None for every page of the book that has an image and has no
                    content type yet.
    """

    page_ids: tuple[PageId, ...] | None = None

    def to_map(self) -> dict[str, Any]:
        """Return the value as the JSON object a job stores.

        :returns: The pages as text, or None for the pages that are not detected yet.
        :rtype: dict[str, Any]
        """
        return {PAGE_IDS_KEY: None if self.page_ids is None else [str(page_id) for page_id in self.page_ids]}

    @classmethod
    def from_map(cls, stored: MetadataMap) -> Self:
        """Read the value from the JSON object ``to_map`` wrote.

        :param stored: The stored object.
        :type stored: MetadataMap
        :returns: The value.
        :rtype: Self
        :raises InvalidParametersError: If the object is not one of a ``detect-content`` job.
        """
        try:
            page_ids = stored[PAGE_IDS_KEY]
            return cls(page_ids=None if page_ids is None else tuple(PageId(UUID(page_id)) for page_id in page_ids))
        except (KeyError, ValueError, TypeError) as error:
            raise _params_error(JobKind.DETECT_CONTENT, error) from error


@frozen(kw_only=True)
class VersionCollection:
    """What a ``collect-versions`` job deletes: the versions that nothing needs, and the previews older than a moment.

    A full run needs no age to go, since it is not shown to anyone once it is not current. A preview is shown by the
    editor that asked for it, so it goes only after some time.

    :ivar previews_older_than: Previews created before this moment may be deleted.
    """

    previews_older_than: datetime

    def to_map(self) -> dict[str, Any]:
        """Return the value as the JSON object a job stores.

        :returns: The moment as ISO 8601 text.
        :rtype: dict[str, Any]
        """
        return {'previews_older_than': self.previews_older_than.isoformat()}

    @classmethod
    def from_map(cls, stored: MetadataMap) -> Self:
        """Read the value from the JSON object ``to_map`` wrote.

        :param stored: The stored object.
        :type stored: MetadataMap
        :returns: The value.
        :rtype: Self
        :raises InvalidParametersError: If the object is not one of a ``collect-versions`` job.
        """
        try:
            return cls(previews_older_than=datetime.fromisoformat(stored['previews_older_than']))
        except (KeyError, ValueError, TypeError) as error:
            raise _params_error(JobKind.COLLECT_VERSIONS, error) from error


@frozen(kw_only=True)
class Job:
    """A background job and how far it has come, held in the memory of the API process and never stored.

    A job is made of tasks, and its progress counts them. A task is the processing of one page by one plugin, or for an
    import the whole import, which reports its files and scans inside the task.

    :ivar id: Identifier of the job.
    :ivar kind: What the job does.
    :ivar state: Where the job is in its life cycle.
    :ivar progress: How many of its tasks are done.
    :ivar error: Why the job failed, shown to the user, or empty.
    :ivar request: The staged files an import job was asked to import, which its run needs in memory, or None for a
                   job that takes none.
    :ivar params: What a processing job was asked to do, in the form its value class writes, empty for a job without.
    :ivar created_at: When the job was queued.
    :ivar started_at: When its book began to run the job, or None while it is queued.
    :ivar finished_at: When the job reached a final state, or None before.
    """

    id: JobId
    kind: JobKind
    state: JobState = JobState.QUEUED
    progress: Progress = field(factory=Progress)
    error: str = ''
    request: ImportRequest | None = None
    params: MetadataMap = field(factory=dict)
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def stage(self) -> Stage | None:
        """Name the stage the job runs, or None for a job that runs none or whose parameters cannot be read.

        :returns: The stage of a ``run-stage`` job.
        :rtype: Stage | None
        """
        if self.kind is not JobKind.RUN_STAGE:
            return None
        try:
            return StageRun.from_map(self.params).stage
        except InvalidParametersError:
            return None
