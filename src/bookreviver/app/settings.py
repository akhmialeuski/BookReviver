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


class JobBroker(enum.StrEnum):
    """Which Taskiq broker runs background jobs."""

    IN_PROCESS = 'in-process'
    REDIS = 'redis'


class OAuthClient(BaseModel):
    """Credentials of one OAuth application; a provider is enabled when both are set."""

    client_id: str = ''
    client_secret: SecretStr = SecretStr('')

    @property
    def enabled(self) -> bool:
        """Whether the provider can be offered on the sign-in page."""
        return bool(self.client_id and self.client_secret.get_secret_value())


class AuthSettings(BaseModel):
    """Accounts, sessions and social sign-in."""

    secret: SecretStr = Field(description='Signs verification and reset tokens, and the CSRF cookie')
    session_lifetime_seconds: PositiveInt = 30 * 24 * 60 * 60
    cookie_secure: bool = True
    google: OAuthClient = OAuthClient()
    facebook: OAuthClient = OAuthClient()
    x: OAuthClient = OAuthClient()


class MailSettings(BaseModel):
    """Outgoing mail; without an SMTP host, messages are written to the log."""

    smtp_host: str = ''
    smtp_port: PositiveInt = 587
    smtp_username: str = ''
    smtp_password: SecretStr = SecretStr('')
    sender: str = 'BookReviver <no-reply@localhost>'


class ImagingSettings(BaseModel):
    """Page extraction and tiling."""

    tile_size_px: PositiveInt = 512
    thumbnail_long_side_px: PositiveInt = 320
    jpeg_quality: int = Field(default=90, ge=1, le=100)
    parallel_pages: PositiveInt = 4


class Settings(BaseSettings):
    """All runtime configuration of the API server and the workers."""

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
