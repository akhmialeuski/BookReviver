"""The ASGI application served by ``fastapi dev`` and ``fastapi run``, built from the environment."""

import logging

import bookreviver
from bookreviver.app.main import create_app

# Uvicorn sets up only its own loggers, so without a handler on the root logger and a level on ours, the records
# of BookReviver, the mail the log mailer writes among them, would never be shown
logging.basicConfig()
logging.getLogger(bookreviver.__name__).setLevel(logging.INFO)

app = create_app()
