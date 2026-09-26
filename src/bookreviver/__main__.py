"""Run the development server: ``uv run bookreviver``."""

import uvicorn

from bookreviver.config import Settings


def main() -> None:
    """Serve the application on the configured host and port."""
    settings = Settings()
    uvicorn.run('bookreviver.app:create_app', factory=True, host=settings.host, port=settings.port)


if __name__ == '__main__':
    main()
