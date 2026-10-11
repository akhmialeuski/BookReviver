"""Security of the API: CSRF protection by a pure ASGI middleware, and slowapi limits on the sign-in routes."""

import hashlib
import hmac
import secrets
from http.cookies import SimpleCookie
from typing import TYPE_CHECKING

from fastapi import Request, params
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse

from bookreviver.api.problems import Forbidden
from bookreviver.app.providers.accounts import AUTH_BACKEND, SESSION_COOKIE

if TYPE_CHECKING:
    from fastapi import FastAPI
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

    from bookreviver.app.settings import Settings

# Per client address and route, in the notation of the limits package that slowapi uses
SIGN_IN_ATTEMPTS: str = '10/minute;100/hour'
CSRF_FAILED: str = 'The request carries no valid CSRF token. Reload the page and try again.'
PROBLEM_MEDIA_TYPE: str = 'application/problem+json'
# The cookie that holds the token and the header that sends it back, which the page script of the frontend relies on
CSRF_COOKIE: str = 'csrftoken'
CSRF_HEADER: str = 'x-csrftoken'
# Requests that change nothing, so a forged one does no harm
SAFE_METHODS: frozenset[str] = frozenset({'GET', 'HEAD', 'OPTIONS', 'TRACE'})
# The ASGI message that starts an answer and carries its headers, and the name of the header that sets a cookie
RESPONSE_START: str = 'http.response.start'
SET_COOKIE: str = 'set-cookie'
# The CSRF cookie goes along with a top-level navigation but not with a cross-site form post
CSRF_COOKIE_SAMESITE: str = 'lax'
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


class CSRFMiddleware:
    """Double-submit CSRF protection as a pure ASGI middleware, refusing with a problem document.

    A token is a random part and its HMAC-SHA256 signature made with the application secret, so only this server can
    issue one. A mutating request that carries the session cookie, or posts to a sign-in route, must send in the
    header the very token it holds in the cookie. A browser holding no token this server signed, which every browser
    has after the server is set up again with a new secret, gets a new one with any answer, the refusal included, so
    the next attempt passes.
    """

    def __init__(self, app: ASGIApp, *, secret: str, sign_in_paths: frozenset[str], secure: bool) -> None:
        """Protect ``app``, signing tokens with ``secret``.

        :param app: The ASGI application to protect.
        :type app: ASGIApp
        :param secret: Key that signs the tokens.
        :type secret: str
        :param sign_in_paths: Paths that are checked whether or not the request carries the session cookie.
        :type sign_in_paths: frozenset[str]
        :param secure: Whether the cookie is sent over HTTPS only.
        :type secure: bool
        """
        self.app = app
        self._key = secret.encode()
        self._sign_in_paths = sign_in_paths
        self._secure = secure

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Refuse a mutating request without a matching token, and hand a new token to a browser without a valid one.

        :param scope: The ASGI scope of the connection.
        :type scope: Scope
        :param receive: The ASGI receive function of the server.
        :type receive: Receive
        :param send: The ASGI send function of the server.
        :type send: Send
        """
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        token = request.cookies.get(CSRF_COOKIE, '')
        signed = self._is_signed(token)

        async def send_with_token(message: Message) -> None:
            """Add a new token to the start of the answer when the browser holds no valid one.

            :param message: The ASGI message being sent.
            :type message: Message
            """
            if message['type'] == RESPONSE_START and not signed:
                # The ASGI specification leaves the headers of a start message optional
                message.setdefault('headers', [])
                MutableHeaders(scope=message).append(SET_COOKIE, self._new_cookie())
            await send(message)

        checked = scope['method'] not in SAFE_METHODS and (
            scope['path'] in self._sign_in_paths or SESSION_COOKIE in request.cookies
        )
        # Both sides are encoded because compare_digest accepts a str of ASCII only, and a header can carry any byte
        if checked and not (
            signed and hmac.compare_digest(token.encode(), request.headers.get(CSRF_HEADER, '').encode())
        ):
            problem = Forbidden(CSRF_FAILED)
            refusal = JSONResponse(problem.marshal(), status_code=problem.status, media_type=PROBLEM_MEDIA_TYPE)
            await refusal(scope, receive, send_with_token)
            return
        await self.app(scope, receive, send_with_token)

    def new_token(self) -> str:
        """Issue a token: a random part and its signature.

        :returns: The token, made of URL-safe characters and one dot.
        :rtype: str
        """
        nonce = secrets.token_urlsafe()
        return f'{nonce}.{self._signature(nonce)}'

    def _signature(self, nonce: str) -> str:
        """Sign the random part of a token, bound to the cookie so a signature made for another purpose never fits.

        :param nonce: The random part.
        :type nonce: str
        :returns: The hexadecimal HMAC-SHA256 of the random part.
        :rtype: str
        """
        return hmac.new(self._key, f'{CSRF_COOKIE}.{nonce}'.encode(), hashlib.sha256).hexdigest()

    def _is_signed(self, token: str) -> bool:
        """Tell whether this server issued the token.

        :param token: Value of the cookie, empty when the browser sent none.
        :type token: str
        :returns: False for an empty token, a forged one, or one signed with another secret.
        :rtype: bool
        """
        nonce, _, signature = token.rpartition('.')
        return hmac.compare_digest(signature.encode(), self._signature(nonce).encode())

    def _new_cookie(self) -> str:
        """Build the value of the header that sets a new token.

        :returns: The cookie with its attributes, as the ``Set-Cookie`` header carries it.
        :rtype: str
        """
        cookie = SimpleCookie()
        cookie[CSRF_COOKIE] = self.new_token()
        morsel = cookie[CSRF_COOKIE]
        morsel['path'] = '/'
        morsel['samesite'] = CSRF_COOKIE_SAMESITE
        morsel['secure'] = self._secure
        # No HttpOnly flag: the page script reads the cookie and echoes its value in the header
        return morsel.OutputString()


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
    GET, which the middleware never checks, and fastapi-users checks their state against a cookie of its own.

    :param app: Application to install the middleware on, with its routers already included.
    :type app: FastAPI
    :param settings: Application settings the CSRF secret and cookie flags are read from.
    :type settings: Settings
    """
    app.add_middleware(
        CSRFMiddleware,
        secret=settings.auth.secret.get_secret_value(),
        sign_in_paths=frozenset(app.url_path_for(name) for name in SIGN_IN_ROUTES),
        secure=settings.auth.cookie_secure,
    )
