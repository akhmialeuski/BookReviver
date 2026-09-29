"""The ASGI application served by ``fastapi dev`` and ``fastapi run``, built from the environment."""

from bookreviver.app.main import create_app

app = create_app()
