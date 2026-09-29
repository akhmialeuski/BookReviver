"""Settings read once from ``BOOKREVIVER_*`` environment variables or a ``.env`` file."""

import enum
from functools import cached_property
from pathlib import Path

from pydantic import BaseModel, Field, PositiveInt, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class PersistenceBackend(enum.StrEnum):
    """Which persistence adapter the container builds."""

    SQLALCHEMY = 'sqlalchemy'
    MEMORY = 'memory'


class StorageBackend(enum.StrEnum):
    """Which storage adapters the container builds for sources and derived files."""

    LOCAL = 'local'


class JobBroker(enum.StrEnum):
    """Which Taskiq broker runs background jobs."""

    IN_PROCESS = 'in-process'
    REDIS = 'redis'


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
    :ivar google: OAuth application for Google sign-in.
    :ivar facebook: OAuth application for Facebook sign-in.
    :ivar x: OAuth application for X sign-in.
    """

    secret: SecretStr = Field(description='Signs verification and reset tokens, and the CSRF cookie')
    session_lifetime_seconds: PositiveInt = 30 * 24 * 60 * 60
    cookie_secure: bool = True
    google: OAuthClient = OAuthClient()
    facebook: OAuthClient = OAuthClient()
    x: OAuthClient = OAuthClient()


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
    :ivar thumbnail_long_side_px: Longer side of a page thumbnail in pixels.
    :ivar jpeg_quality: JPEG quality from 1 to 100 of rendered pages, tiles and thumbnails.
    :ivar parallel_pages: Largest number of pages an import cuts at the same time.
    """

    tile_size_px: PositiveInt = 512
    thumbnail_long_side_px: PositiveInt = 320
    jpeg_quality: int = Field(default=90, ge=1, le=100)
    parallel_pages: PositiveInt = 4


class Settings(BaseSettings):
    """All runtime configuration of the API server and the workers.

    :ivar public_url: Address the application is reached at from outside.
    :ivar data_dir: Directory holding the SQLite database and the storage root.
    :ivar database_url: SQLAlchemy async database URL, or empty for an SQLite file in ``data_dir``.
    :ivar persistence: Persistence backend the container builds.
    :ivar storage: Storage backend the container builds for sources and derived files.
    :ivar job_broker: Taskiq broker that runs background jobs.
    :ivar redis_url: Redis address of the Redis job broker.
    :ivar max_upload_bytes: Largest total size of one upload, 4 GiB by default.
    :ivar event_queue_size: Undelivered events each event subscriber keeps before dropping the oldest.
    :ivar auth: Accounts, sessions and social sign-in; required, since it holds the signing secret.
    :ivar mail: Outgoing mail.
    :ivar imaging: Page extraction and tiling.
    """

    model_config = SettingsConfigDict(
        env_prefix='BOOKREVIVER_',
        env_nested_delimiter='__',
        env_file='.env',
        extra='ignore',
    )

    public_url: str = 'http://127.0.0.1:8000'
    data_dir: Path = Path('data')
    database_url: str = ''
    persistence: PersistenceBackend = PersistenceBackend.SQLALCHEMY
    storage: StorageBackend = StorageBackend.LOCAL
    job_broker: JobBroker = JobBroker.IN_PROCESS
    redis_url: str = 'redis://localhost:6379/0'
    max_upload_bytes: PositiveInt = 4 * 1024**3
    event_queue_size: PositiveInt = 256
    auth: AuthSettings
    mail: MailSettings = MailSettings()
    imaging: ImagingSettings = ImagingSettings()

    @cached_property
    def resolved_database_url(self) -> str:
        """The configured database URL, or an SQLite file inside the data directory."""
        return self.database_url or f'sqlite+aiosqlite:///{self.data_dir.resolve() / "bookreviver.db"}'

    @cached_property
    def storage_root(self) -> Path:
        """The directory holding sources and derived files."""
        return self.data_dir.resolve() / 'storage'
