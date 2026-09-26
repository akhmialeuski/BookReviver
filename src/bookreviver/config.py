"""Application settings read from the environment."""

from functools import cached_property
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DATA_DIR: Path = Path('data')
DEFAULT_HOST: str = '127.0.0.1'
DEFAULT_PORT: int = 8000
DEFAULT_MAX_UPLOAD_BYTES: int = 4 * 1024**3
DATABASE_FILE_NAME: str = 'bookreviver.db'


class Settings(BaseSettings):
    """Runtime configuration, overridable through ``BOOKREVIVER_*`` variables or a ``.env`` file."""

    model_config = SettingsConfigDict(env_prefix='BOOKREVIVER_', env_file='.env', extra='ignore')

    data_dir: Path = DEFAULT_DATA_DIR
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES

    @cached_property
    def database_url(self) -> str:
        """The SQLAlchemy URL of the SQLite database inside the data directory."""
        return f'sqlite+aiosqlite:///{self.data_dir.resolve() / DATABASE_FILE_NAME}'

    @cached_property
    def projects_dir(self) -> Path:
        """The directory holding one sub-directory of files per project."""
        return self.data_dir.resolve() / 'projects'
