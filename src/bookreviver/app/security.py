"""Security of the API: CSRF protection with starlette-csrf, and slowapi limits on the sign-in routes."""

import re
from typing import TYPE_CHECKING, Literal, override

from fastapi import Request, params
from itsdangerous import BadSignature
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse, Response
from starlette_csrf import CSRFMiddleware

from bookreviver.api.problems import Forbidden
from bookreviver.app.providers.accounts import AUTH_BACKEND, SESSION_COOKIE

if TYPE_CHECKING:
    from fastapi import FastAPI
    from starlette.types import Message, Scope, Send

    from bookreviver.app.settings import Settings

# Per client address and route, in the notation of the limits package that slowapi uses
SIGN_IN_ATTEMPTS: str = '10/minute;100/hour'
CSRF_FAILED: str = 'The request carries no valid CSRF token. Reload the page and try again.'
PROBLEM_MEDIA_TYPE: str = 'application/problem+json'
# The ASGI message that starts an answer and carries its headers, and the raw name of the header that sets a cookie
RESPONSE_START: str = 'http.response.start'
SET_COOKIE: bytes = b'set-cookie'
# The CSRF cookie goes along with a top-level navigation but not with a cross-site form post
CSRF_COOKIE_SAMESITE: Literal['lax'] = 'lax'
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
    """starlette-csrf, answering a failed check with a problem document and replacing a token it cannot read.

    starlette-csrf hands out a token only to a browser that has none. A browser holding a token signed with another
    secret, which every browser has after the server is set up again with a new one, would keep it, and every form it
    sends would fail the check however often the page is reloaded. Here a token the server cannot read counts as
    none: any answer to such a browser brings a new one, the refusal included, so the next attempt passes.
    """

    @override
    async def send(self, message: Message, send: Send, scope: Scope) -> None:
        """Send one message of the answer, adding a new token to its start when the browser has no valid one.

        :param message: The ASGI message being sent.
        :type message: Message
        :param send: The ASGI send function of the server.
        :type send: Send
        :param scope: The ASGI scope of the request.
        :type scope: Scope
        """
        if message['type'] == RESPONSE_START and not self._holds_valid_token(Request(scope)):
            holder = Response()
            self._issue_token(holder)
            headers = MutableHeaders(scope=message)
            headers.raw.extend(header for header in holder.raw_headers if header[0] == SET_COOKIE)
        await send(message)

    @override
    def _get_error_response(self, request: Request) -> Response:
        """Build the answer to a request that fails the CSRF check, with a new token when its own cannot be read.

        :param request: The refused request, whose cookie decides whether the answer brings a new token.
        :type request: Request
        :returns: A 403 problem document.
        :rtype: Response
        """
        problem = Forbidden(CSRF_FAILED)
        response = JSONResponse(problem.marshal(), status_code=problem.status, media_type=PROBLEM_MEDIA_TYPE)
        if not self._holds_valid_token(request):
            self._issue_token(response)
        return response

    def _holds_valid_token(self, request: Request) -> bool:
        """Tell whether the request carries a CSRF cookie that this server signed.

        :param request: The request.
        :type request: Request
        :returns: False for a request without the cookie or with one signed with another secret.
        :rtype: bool
        """
        if (token := request.cookies.get(self.cookie_name)) is None:
            return False
        try:
            self.serializer.loads(token)
        except BadSignature:
            return False
        return True

    def _issue_token(self, response: Response) -> None:
        """Set a new CSRF cookie on a response, with the attributes the middleware was configured with.

        :param response: The response that carries the cookie.
        :type response: Response
        """
        response.set_cookie(
            self.cookie_name,
            self._generate_csrf_token(),
            path=self.cookie_path,
            domain=self.cookie_domain,
            secure=self.cookie_secure,
            httponly=self.cookie_httponly,
            samesite=CSRF_COOKIE_SAMESITE,
        )


async def count_sign_in_attempt(request: Request) -> None:
    """Count one attempt; the slowapi limit wrapped around this raises once the caller has used up the limit.

    :param request: The request being counted; slowapi finds it by this parameter name.
    :type request: Request
    """


def sign_in_throttle() -> params.Depends:
    """Build the dependency that limits sign-in attempts per client address, with counters of its own.

    :returns: Dependency to attach to the sign-in routers.
    :rtype: params.Depends
    """
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
        cookie_samesite=CSRF_COOKIE_SAMESITE,
    )
