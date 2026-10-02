"""The application the end-to-end scenarios run against: the real one, with a fake social provider in place of Google.

Playwright starts it with ``uvicorn tests.helpers.e2e_app:app`` on a fresh database. Everything but the provider is
the production wiring, built from the environment: the routers, the state cookie, the account linking, the session and
the built frontend. The provider is a ``FakeOAuth2`` whose consent page is a route of this same server, which returns
the browser to the callback page at once, so a scenario goes through the real redirects without any network. Real
Google and Facebook are never called.
"""

import logging

from starlette.routing import Route

import bookreviver
from bookreviver.app import security
from bookreviver.app.main import create_app
from bookreviver.app.settings import Settings
from tests.helpers.fake_oauth import CONSENT_PATH, FakeOAuth2, consent_route

E2E_SIGN_IN_ATTEMPTS: str = '1000/minute'

# As in bookreviver.app.asgi: the mail the log mailer writes is the way the scenarios read their links
logging.basicConfig()
logging.getLogger(bookreviver.__name__).setLevel(logging.INFO)

# Parallel scenarios register, confirm and sign in from one address far faster than the production limit of ten a minute
# allows. The limit itself is tested in tests/accounts/test_security.py, and it is read when the application is built,
# so it is raised here, before that, for this server alone
security.SIGN_IN_ATTEMPTS = E2E_SIGN_IN_ATTEMPTS

settings = Settings()
app = create_app(
    settings, social_clients=[FakeOAuth2(authorize_endpoint=f'{settings.public_url.rstrip("/")}{CONSENT_PATH}')]
)
# The frontend's catch-all answers every address that accepts HTML, so the consent page goes in front of it
app.router.routes.insert(0, Route(CONSENT_PATH, consent_route()))
