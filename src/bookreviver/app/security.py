"""Security of the API: CSRF protection with starlette-csrf, and slowapi limits on the sign-in routes."""

import re
from typing import TYPE_CHECKING, override

from fastapi import Request, params
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.responses import JSONResponse
from starlette_csrf import CSRFMiddleware

from bookreviver.api.problems import Forbidden
from bookreviver.app.providers.accounts import AUTH_BACKEND, SESSION_COOKIE

if TYPE_CHECKING:
    from fastapi import FastAPI
    from starlette.responses import Response

    from bookreviver.app.settings import Settings

# Per client address and route, in the notation of the limits package that slowapi uses
SIGN_IN_ATTEMPTS: str = '10/minute;100/hour'
CSRF_FAILED: str = 'The request carries no valid CSRF token. Reload the page and try again.'
PROBLEM_MEDIA_TYPE: str = 'application/problem+json'
# Routes a signed-out visitor posts to, named by fastapi-users
SIGN_IN_ROUTES: frozenset[str] = frozenset(
    {
        f'auth:{AUTH_BACKEND}.login',
        'register:register',
        'verify:request-token',
        'verify:verify',
        'reset:forgot_password',
        'reset:reset_password',
    }
)


class ProblemCSRFMiddleware(CSRFMiddleware):
    """starlette-csrf, answering a failed check with a problem document like every other error."""

    @override
    def _get_error_response(self, request: Request) -> Response:
        problem = Forbidden(CSRF_FAILED)
        return JSONResponse(problem.marshal(), status_code=problem.status, media_type=PROBLEM_MEDIA_TYPE)


async def count_sign_in_attempt(request: Request) -> None:
    """Count one attempt; the slowapi limit wrapped around this raises once the caller has used up the limit."""


def sign_in_throttle() -> params.Depends:
    """Build the dependency that limits sign-in attempts per client address, with counters of its own."""
    limiter = Limiter(key_func=get_remote_address)
    return params.Depends(limiter.limit(SIGN_IN_ATTEMPTS)(count_sign_in_attempt))


def install_security(app: FastAPI, settings: Settings) -> None:
    """Add CSRF protection; call it once the routers are included, because it finds the sign-in routes by name.

    The double-submit check covers every mutating request that carries the session cookie, the only kind a forged
    cross-site request could act with, and the sign-in routes, so a forged form cannot sign a visitor in to an
    account of the forger. OAuth callbacks stay exempt without being listed: the provider redirects to them with a
    GET, which starlette-csrf never checks, and fastapi-users checks their state against a cookie of its own.

    :param app: Application to install the middleware on, with its routers already included.
    :type app: FastAPI
    :param settings: Application settings the CSRF secret and cookie flags are read from.
    :type settings: Settings
    """
    required = [re.compile(f'{re.escape(app.url_path_for(name))}$') for name in SIGN_IN_ROUTES]
    app.add_middleware(
        ProblemCSRFMiddleware,
        secret=settings.auth.secret.get_secret_value(),
        required_urls=required,
        sensitive_cookies={SESSION_COOKIE},
        cookie_secure=settings.auth.cookie_secure,
        cookie_samesite='lax',
    )
