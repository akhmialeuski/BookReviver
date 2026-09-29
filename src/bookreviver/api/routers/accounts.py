"""Sign-in, registration, account settings and provider credentials."""

from typing import TYPE_CHECKING, Any
from uuid import UUID

from attrs import frozen
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter
from fastapi_users.models import UserProtocol

from bookreviver.api.schemas.accounts import AccountCreate, AccountRead, AccountUpdate

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from fastapi import params
    from fastapi_users import FastAPIUsers
    from fastapi_users.authentication import AuthenticationBackend
    from fastapi_users.jwt import SecretType
    from httpx_oauth.oauth2 import BaseOAuth2

ACCOUNTS_TAG: str = 'accounts'
AUTH_TAG: str = 'auth'

router = APIRouter(tags=[ACCOUNTS_TAG], route_class=DishkaRoute)


@frozen(kw_only=True)
class AccountRoutes[UserT: UserProtocol[UUID]]:
    """The fastapi-users routes under ``/auth`` and ``/users``, from objects the application builds from settings.

    :ivar users: The fastapi-users object that builds the routers and the current-user dependency.
    :ivar backend: Authentication backend, the cookie transport with the database strategy.
    :ivar oauth_clients: Clients of the social providers to offer, one OAuth router each.
    :ivar state_secret: Key signing the state parameter of the OAuth routes.
    :ivar secure_cookies: Whether the OAuth CSRF cookie requires HTTPS.
    :ivar throttle: Dependency counting attempts at the routes that take a password or a mailed token.
    """

    users: FastAPIUsers[UserT, UUID]
    backend: AuthenticationBackend[UserT, UUID]
    oauth_clients: Sequence[BaseOAuth2[Any]]
    state_secret: SecretType
    secure_cookies: bool
    # Counts attempts at the routes that take a password or a mailed token
    throttle: params.Depends

    @property
    def current_user(self) -> Callable[..., Any]:
        """The dependency returning the signed-in user, who must be active and verified.

        :returns: Dependency that yields the user of the request's session cookie.
        :rtype: Callable[..., Any]
        """
        return self.users.current_user(active=True, verified=True)

    def router(self) -> APIRouter:
        """Build the routes; signing in with a password requires a verified email address.

        :returns: Router holding sign-in, registration, verification, reset, OAuth and user routes.
        :rtype: APIRouter
        """
        auth = APIRouter(prefix='/auth', tags=[AUTH_TAG])
        throttled = [self.throttle]
        auth.include_router(
            self.users.get_auth_router(self.backend, requires_verification=True), dependencies=throttled
        )
        auth.include_router(self.users.get_register_router(AccountRead, AccountCreate), dependencies=throttled)
        auth.include_router(self.users.get_verify_router(AccountRead), dependencies=throttled)
        auth.include_router(self.users.get_reset_password_router(), dependencies=throttled)
        for client in self.oauth_clients:
            oauth = self.users.get_oauth_router(
                client,
                self.backend,
                self.state_secret,
                associate_by_email=True,
                is_verified_by_default=True,
                csrf_token_cookie_secure=self.secure_cookies,
            )
            auth.include_router(oauth, prefix=f'/{client.name}')
        routes = APIRouter()
        routes.include_router(auth)
        users = self.users.get_users_router(AccountRead, AccountUpdate, requires_verification=True)
        routes.include_router(users, prefix='/users', tags=[ACCOUNTS_TAG])
        return routes
