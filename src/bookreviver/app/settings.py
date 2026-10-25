"""Settings read once from ``BOOKREVIVER_*`` environment variables or a ``.env`` file."""

import enum
import os
from functools import cached_property
from pathlib import Path
from typing import Self

from pydantic import BaseModel, Field, PositiveFloat, PositiveInt, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from bookreviver.domain.enums import WorkerPool
from bookreviver.ports.persistence import DEFAULT_CHANGE_WAIT_SECONDS

TRACING_NEEDS_PATH: str = 'Tracing is enabled, so tracing.export_path must name the file the spans are written to.'
TRACING_PATH_WITHOUT_TRACING: str = (
    'tracing.export_path is set while tracing is disabled. Enable tracing, or remove the path.'
)
STEP_PROCESSES_NEED_PREVIEW_SHARE: str = (
    'processing.preview_step_processes must be smaller than processing.step_processes, '
    'so that full runs keep at least one process.'
)

# The share of the cores the processes of the steps take by default, two thirds, and the fewest they are
STEP_PROCESSES_CORES_NUMERATOR: int = 2
STEP_PROCESSES_CORES_DENOMINATOR: int = 3
MIN_STEP_PROCESSES: int = 2


class StorageBackend(enum.StrEnum):
    """Which storage adapters the container builds for sources and derived files."""

    LOCAL = 'local'


class OAuthClient(BaseModel):
    """Credentials of one OAuth application; a provider is enabled when both are set.

    :ivar client_id: Client identifier the provider issued, or empty to disable the provider.
    :ivar client_secret: Client secret the provider issued, never printed or logged.
    """

    client_id: str = ''
    client_secret: SecretStr = SecretStr('')

    @property
    def enabled(self) -> bool:
        """Whether the provider can be offered on the sign-in page."""
        return bool(self.client_id and self.client_secret.get_secret_value())


class AuthSettings(BaseModel):
    """Accounts, sessions and social sign-in.

    :ivar secret: Key signing verification and reset tokens and the CSRF cookie; required.
    :ivar session_lifetime_seconds: How long a sign-in lasts, 30 days by default.
    :ivar cookie_secure: Whether the session cookie requires HTTPS; off only for local development over HTTP.
    :ivar password_hash_concurrency: Most password hashes and checks run at once, each in a worker thread of its own.
                                     An argon2id hash holds 64 MiB and four lanes, so the bound keeps a burst of
                                     sign-ins from taking the memory and the cores, and the threads file work shares.
    :ivar google: OAuth application for Google sign-in.
    :ivar facebook: OAuth application for Facebook sign-in.
    """

    secret: SecretStr = Field(description='Signs verification and reset tokens, and the CSRF cookie')
    session_lifetime_seconds: PositiveInt = 30 * 24 * 60 * 60
    cookie_secure: bool = True
    password_hash_concurrency: PositiveInt = 2
    google: OAuthClient = OAuthClient()
    facebook: OAuthClient = OAuthClient()


class MailSettings(BaseModel):
    """Outgoing mail; without an SMTP host, messages are written to the log.

    :ivar smtp_host: SMTP server, or empty to write messages to the log instead.
    :ivar smtp_port: SMTP port, 587 for submission with STARTTLS.
    :ivar smtp_username: SMTP user name, or empty for an unauthenticated server.
    :ivar smtp_password: SMTP password, never printed or logged.
    :ivar sender: From address of every message.
    """

    smtp_host: str = ''
    smtp_port: PositiveInt = 587
    smtp_username: str = ''
    smtp_password: SecretStr = SecretStr('')
    sender: str = 'BookReviver <no-reply@localhost>'


class ImagingSettings(BaseModel):
    """Page extraction and tiling.

    :ivar tile_size_px: Side of a square IIIF tile in pixels.
    :ivar preview_long_side_px: Longer side of a preview in pixels, for interactive previews of processing steps.
    :ivar thumbnail_long_side_px: Longer side of a page thumbnail in pixels.
    :ivar jpeg_quality: JPEG quality from 1 to 100 of rendered pages, tiles, previews and thumbnails.
    :ivar parallel_scans: Largest number of scans an import cuts at the same time.
    :ivar djvulibre_timeout_s: Seconds one call of a DjVuLibre tool may run before the file is refused as damaged.
    """

    tile_size_px: PositiveInt = 512
    preview_long_side_px: PositiveInt = 2048
    thumbnail_long_side_px: PositiveInt = 320
    jpeg_quality: int = Field(default=90, ge=1, le=100)
    parallel_scans: PositiveInt = 4
    djvulibre_timeout_s: PositiveInt = 120


def default_step_processes() -> int:
    """Count the processes the steps of processors run in when no setting names a number.

    The cores are those this process may run on, so a CPU affinity set by ``taskset`` or a container limits the pool.
    ``os.cpu_count`` would count every core of the machine instead.

    :returns: Two thirds of the cores this process may use, and at least two.
    :rtype: int
    """
    cores = os.process_cpu_count() or MIN_STEP_PROCESSES
    return max(MIN_STEP_PROCESSES, cores * STEP_PROCESSES_CORES_NUMERATOR // STEP_PROCESSES_CORES_DENOMINATOR)


class ProcessingSettings(BaseModel):
    """Processing of pages by plugins.

    :ivar worker_pools: Pools of workers this process serves, whose processors the catalogue offers.
    :ivar preview_retention_hours: Hours a preview is kept before a collection may delete it.
    :ivar models_dir: Directory of the files of neural models, into which a processor downloads a model the first time
                      it runs.
    :ivar step_processes: Most processes the steps of processors run in at the same time, two thirds of the cores by
                          default and at least two, so the work of OpenCV and Pillow never holds the interpreter of
                          the API process.
    :ivar preview_step_processes: Processes of ``step_processes`` that run previews only, so a preview never waits
                                  behind the full runs of other books. The full runs get the rest, which is why it
                                  must be smaller than ``step_processes``.
    :ivar recipes_file: JSON file of the recipes every stage of a new book starts with, the file packaged with the
                        application by default. A file of your own replaces it as a whole.
    """

    worker_pools: frozenset[WorkerPool] = frozenset(WorkerPool)
    preview_retention_hours: PositiveInt = 24
    models_dir: Path = Path('data/models')
    step_processes: PositiveInt = Field(default_factory=default_step_processes)
    preview_step_processes: PositiveInt = 1
    recipes_file: Path = Path(__file__).with_name('recipes.json')

    # A literal mode: the pydantic plugin of mypy reads the mode only from a literal, even a Final constant fails it
    @model_validator(mode='after')
    def _require_slots_for_full_runs(self) -> Self:
        """Check that the processes of previews leave some for the full runs.

        :returns: The settings, once checked.
        :rtype: Self
        :raises ValueError: If the previews would take every process.
        """
        if self.preview_step_processes >= self.step_processes:
            raise ValueError(STEP_PROCESSES_NEED_PREVIEW_SHARE)
        return self


class JobsSettings(BaseModel):
    """The board of background jobs, which lives in the memory of the API process.

    :ivar kept_ended: Most ended jobs the board holds for each book, the latest ones, which the activity list shows.
    """

    kept_ended: PositiveInt = 20


class TracingSettings(BaseModel):
    """Measurements of the backend, written as OpenTelemetry spans into a file; off by default.

    :ivar enabled: Whether spans are recorded. Off, nothing is recorded and nothing is written.
    :ivar export_path: The JSON lines file spans are appended to, one object per span. Required when ``enabled``, and
                       refused otherwise, so a path left in the environment cannot suggest that measurements are on.
    :ivar loop_lag_interval_seconds: Seconds between the ticks of the event-loop lag probe, which runs only while
                                     spans are recorded.
    """

    enabled: bool = False
    export_path: Path | None = None
    loop_lag_interval_seconds: PositiveFloat = 0.1

    @model_validator(mode='after')
    def _require_path_when_enabled(self) -> Self:
        """Check that a path is given exactly when the measurements are on.

        :returns: The settings, once checked.
        :rtype: Self
        :raises ValueError: If the measurements are on without a path, or off with one.
        """
        if self.enabled and self.export_path is None:
            raise ValueError(TRACING_NEEDS_PATH)
        if not self.enabled and self.export_path is not None:
            raise ValueError(TRACING_PATH_WITHOUT_TRACING)
        return self

    @property
    def sink(self) -> Path | None:
        """The file spans are written to, or None when the measurements are off."""
        return self.export_path if self.enabled else None


class ChangeWaitSettings(BaseModel):
    """How long a change of the persistence port waits for the writer before it gives up as busy.

    The limit covers only the wait for the turn of the writer, never the work of the change itself. The database
    also opens every connection with the job limit as the busy timeout of SQLite, which matters only when a process
    other than the application, such as a migration run by hand, holds the write lock of the file.

    :ivar request_seconds: Limit of a catalogue database that serves an HTTP request, 3 seconds by default, so an
                           interface never hangs for the long wait of a job and is told at once to try again.
    :ivar job_seconds: Limit of every other catalogue database, the background jobs, the work of the startup and every
                       change of a book, 30 seconds by default, so a job does not lose its work to a short wait.
    """

    request_seconds: PositiveFloat = 3.0
    job_seconds: PositiveFloat = DEFAULT_CHANGE_WAIT_SECONDS


class Settings(BaseSettings):
    """All runtime configuration of the API server and the workers.

    :ivar public_url: Address the application is reached at from outside.
    :ivar data_dir: Directory holding the catalogue and the storage root, under which every book has its directory.
    :ivar frontend_dir: Directory of the built frontend, served at ``/`` when it exists; ``npm --prefix frontend run
                        build`` writes it.
    :ivar catalogue_url: SQLAlchemy URL of the SQLite database of the catalogue, or empty for ``catalogue.db`` in
                         ``data_dir``.
    :ivar book_idle_seconds: How long the database of a book stays open after its last use, ten minutes by default.
    :ivar storage: Storage backend the container builds for sources and derived files.
    :ivar max_upload_bytes: Largest total size of one upload, 4 GiB by default.
    :ivar max_upload_files: Largest number of files in one upload, 10000 by default.
    :ivar event_queue_size: Undelivered events each event subscriber keeps before dropping the oldest.
    :ivar change_wait: How long a change of a catalogue database waits for the writer, one limit for the catalogue
                       databases of HTTP requests and one for those of jobs, before it is refused as busy.
    :ivar auth: Accounts, sessions and social sign-in; required, since it holds the signing secret.
    :ivar mail: Outgoing mail.
    :ivar imaging: Page extraction and tiling.
    :ivar processing: Processing of pages by plugins.
    :ivar jobs: The board of background jobs.
    :ivar tracing: Measurements of the backend, written as spans into a file.
    """

    model_config = SettingsConfigDict(
        env_prefix='BOOKREVIVER_',
        env_nested_delimiter='__',
        env_file='.env',
        extra='ignore',
    )

    public_url: str = 'http://127.0.0.1:8000'
    data_dir: Path = Path('data')
    frontend_dir: Path = Path('frontend/dist')
    catalogue_url: str = ''
    book_idle_seconds: PositiveFloat = 600.0
    storage: StorageBackend = StorageBackend.LOCAL
    max_upload_bytes: PositiveInt = 4 * 1024**3
    max_upload_files: PositiveInt = 10_000
    event_queue_size: PositiveInt = 256
    change_wait: ChangeWaitSettings = ChangeWaitSettings()
    auth: AuthSettings
    mail: MailSettings = MailSettings()
    imaging: ImagingSettings = ImagingSettings()
    processing: ProcessingSettings = ProcessingSettings()
    jobs: JobsSettings = JobsSettings()
    tracing: TracingSettings = TracingSettings()

    @cached_property
    def resolved_catalogue_url(self) -> str:
        """The configured URL of the catalogue, or an SQLite file inside the data directory."""
        return self.catalogue_url or f'sqlite:///{self.data_dir.resolve() / "catalogue.db"}'

    @cached_property
    def storage_root(self) -> Path:
        """The directory holding the directory of every book, with its database, its sources and its derived files."""
        return self.data_dir.resolve() / 'storage'
