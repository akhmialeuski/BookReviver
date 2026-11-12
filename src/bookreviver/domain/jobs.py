"""Background jobs: their kinds, their state and progress, and the parameters each kind runs with."""

from typing import TYPE_CHECKING, ClassVar, Self

from attrs import field, frozen, validators

from bookreviver.domain.errors import InvalidParametersError
from bookreviver.domain.labels import LabeledStrEnum
from bookreviver.domain.runs import RunMode

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from bookreviver.domain.ids import JobId, PageId, PageVersionId
    from bookreviver.domain.recipes import Step
    from bookreviver.domain.sources import SourceFile
    from bookreviver.domain.stages import Stage


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
        """Return the kinds of job that read and write the versions of pages.

        A book runs one job at a time, in the order the jobs were put, so two of them never overlap. A change the user
        makes to what is current, such as choosing another version of a page, is refused while one of them is queued or
        running, since the job would read or write over it.

        :returns: A run, a preview, a measure of the book, a tile cutting and a collection of old versions.
        :rtype: frozenset[JobKind]
        """
        return frozenset({cls.RUN_STAGE, cls.PREVIEW_STEP, cls.MEASURE_BOOK, cls.CUT_TILES, cls.COLLECT_VERSIONS})

    @classmethod
    def replacing_previews(cls) -> frozenset[JobKind]:
        """Return the kinds of job that cancel the previews of the book when they are asked for.

        A preview is a look at one page that changes nothing that is current. A run and a measure of the book are what
        the reader pressed a button for, so a preview in their way gives the book up, and a new preview takes the place
        of the one before it, since the editor shows the latest look only. A tile cutting or a collection waits its
        turn and cancels nothing.

        :returns: A run, a measure of the book and a preview.
        :rtype: frozenset[JobKind]
        """
        return frozenset({cls.RUN_STAGE, cls.MEASURE_BOOK, cls.PREVIEW_STEP})


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


@frozen(kw_only=True)
class TileCut:
    """What a ``cut-tiles`` job cuts: the tile pyramids of some versions.

    :ivar version_ids: Versions to cut.
    """

    version_ids: tuple[PageVersionId, ...] = field(validator=validators.min_len(1))


@frozen(kw_only=True)
class ContentDetection:
    """What a ``detect-content`` job detects: the content type of some pages, or of those that have none yet.

    :ivar page_ids: Pages to detect, which includes the pages the user set by hand and the pages of every kind, since
                    the user asked for them by name, or None for every page of the book that has an image and has no
                    content type yet.
    """

    page_ids: tuple[PageId, ...] | None = None


@frozen(kw_only=True)
class VersionCollection:
    """What a ``collect-versions`` job deletes: the versions that nothing needs, and the previews older than a moment.

    A full run needs no age to go, since it is not shown to anyone once it is not current. A preview is shown by the
    editor that asked for it, so it goes only after some time.

    :ivar previews_older_than: Previews created before this moment may be deleted.
    """

    previews_older_than: datetime


# What a job was asked to do: the value of its kind, which says the step or the stage, the pages and the parameters
type JobWork = ImportRequest | StageRun | StepPreview | TileCut | ContentDetection | VersionCollection


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
    :ivar work: What the job was asked to do, as the value of its kind: the stage and the pages of a run, the steps
                of a preview, the files of an import, or None for a job that takes nothing, such as a measure of the
                book.
    :ivar created_at: When the job was queued.
    :ivar started_at: When its book began to run the job, or None while it is queued.
    :ivar finished_at: When the job reached a final state, or None before.
    """

    id: JobId
    kind: JobKind
    state: JobState = JobState.QUEUED
    progress: Progress = field(factory=Progress)
    error: str = ''
    work: JobWork | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def asked[WorkT](self, work_type: type[WorkT]) -> WorkT:
        """Give what the job was asked to do, as the value the worker of its kind reads.

        :param work_type: The class of the value the worker expects, such as ``StageRun``.
        :type work_type: type[WorkT]
        :returns: The work of the job.
        :rtype: WorkT
        :raises InvalidParametersError: If the job was put with no work, or with the work of another kind.
        """
        if not isinstance(self.work, work_type):
            err_msg = f'A {self.kind} job was not asked for a {work_type.__name__}.'
            raise InvalidParametersError(err_msg)
        return self.work

    @property
    def stage(self) -> Stage | None:
        """Name the stage the job runs, or None for a job that runs none.

        :returns: The stage of a ``run-stage`` job.
        :rtype: Stage | None
        """
        return self.work.stage if self.kind is JobKind.RUN_STAGE and isinstance(self.work, StageRun) else None
