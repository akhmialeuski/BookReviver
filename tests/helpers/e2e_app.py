"""The application the end-to-end scenarios run against: the real one, with a fake social provider in place of Google.

Playwright starts it with ``uvicorn tests.helpers.e2e_app:app`` on a fresh database. Everything but the provider is
the production wiring, built from the environment: the routers, the state cookie, the account linking, the session and
the built frontend. The provider is a ``FakeOAuth2`` whose consent page is a route of this same server, which returns
the browser to the callback page at once, so a scenario goes through the real redirects without any network. Real
Google and Facebook are never called.
"""

import logging
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from starlette.responses import JSONResponse
from starlette.routing import Route

import bookreviver
from bookreviver.adapters.persistence.sqlalchemy.tables import PageRow, PageVersionRow
from bookreviver.app import security
from bookreviver.app.main import create_app
from bookreviver.app.settings import Settings
from tests.helpers.fake_oauth import CONSENT_PATH, FakeOAuth2, consent_route

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

E2E_SIGN_IN_ATTEMPTS: str = '1000/minute'
AGE_VERSIONS_PATH: str = '/e2e/age-versions'
PROJECT_FIELD: str = 'project_id'
DAYS_FIELD: str = 'days'
MOVED_FIELD: str = 'moved'

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


async def age_versions(request: Request) -> Response:
    """Make every version of one project older by some days, so a collection finds them past their retention.

    The retention of the versions is thirty days at least, and a scenario cannot wait for it. It asks the server to move
    the creation time of the versions of its own project back, then queues the collection through the real route.

    :param request: The request, whose query names the ``project_id`` and the number of ``days``.
    :type request: Request
    :returns: The number of versions moved.
    :rtype: Response
    """
    project_id = UUID(request.query_params[PROJECT_FIELD])
    older = timedelta(days=int(request.query_params[DAYS_FIELD]))
    engine = create_async_engine(settings.resolved_database_url)
    async with AsyncSession(engine) as session:
        rows = (
            await session.scalars(
                select(PageVersionRow)
                .join(PageRow, PageVersionRow.page_id == PageRow.id)
                .where(PageRow.project_id == project_id)
            )
        ).all()
        for row in rows:
            row.created_at -= older
        await session.commit()
    await engine.dispose()
    return JSONResponse({MOVED_FIELD: len(rows)})


# The frontend's catch-all answers every address that accepts HTML, so the routes of the scenarios go in front of it
app.router.routes.insert(0, Route(CONSENT_PATH, consent_route()))
app.router.routes.insert(0, Route(AGE_VERSIONS_PATH, age_versions, methods=['POST']))
