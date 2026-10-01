"""Sign-in, registration, account settings and provider credentials."""

from typing import TYPE_CHECKING, Any
from uuid import UUID

from attrs import frozen
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter
from fastapi_users.models import UserProtocol

from bookreviver.api.schemas.accounts import AccountCreate, AccountRead, AccountUpdate, SignInProvider

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from fastapi import params
    from fastapi_users import FastAPIUsers
    from fastapi_users.authentication import AuthenticationBackend
    from fastapi_users.jwt import SecretType
    from httpx_oauth.oauth2 import BaseOAuth2

ACCOUNTS_TAG: str = 'accounts'
AUTH_TAG: str = 'auth'
# Page of the web interface a provider returns the browser to, which hands the code and the state to the API
OAUTH_CALLBACK_PAGE: str = '{public_url}/auth/{name}/callback'

router = APIRouter(tags=[ACCOUNTS_TAG], route_class=DishkaRoute)


@frozen(kw_only=True)
class AccountRoutes[UserT: UserProtocol[UUID]]:
    """The fastapi-users routes under ``/auth`` and ``/users``, from objects the application builds from settings.

    :ivar users: The fastapi-users object that builds the routers and the current-user dependency.
    :ivar backend: Authentication backend, the cookie transport with the database strategy.
    :ivar oauth_clients: Clients of the social providers to offer, one OAuth router each.
    :ivar state_secret: Key signing the state parameter of the OAuth routes.
    :ivar public_url: Address of the web interface, whose page ``/auth/{name}/callback`` each provider returns to.
    :ivar secure_cookies: Whether the OAuth CSRF cookie requires HTTPS.
    :ivar throttle: Dependency counting attempts at the routes that take a password or a mailed token.
    """

    users: FastAPIUsers[UserT, UUID]
    backend: AuthenticationBackend[UserT, UUID]
    oauth_clients: Sequence[BaseOAuth2[Any]]
    state_secret: SecretType
    public_url: str
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

    def list_providers(self) -> list[SignInProvider]:
        """List the social sign-in providers this server offers.

        The list is public, so the sign-in screen can show a button per provider before anyone is signed in. It holds
        a name and a label only, never a client identifier or a secret.

        \N{FORM FEED}
        :returns: The enabled providers in the order they are routed, empty when none has credentials.
        :rtype: list[SignInProvider]
        """
        # Branded httpx-oauth clients carry the provider's own spelling of its name, and an unbranded one gets its name
        return [
            SignInProvider(name=client.name, label=getattr(client, 'display_name', client.name.title()))
            for client in self.oauth_clients
        ]

    def router(self) -> APIRouter:
        """Build the routes; signing in with a password requires a verified email address.

        Each OAuth router is told the page of the web interface the provider returns to, and not its own callback
        route, because that route answers a successful sign-in with ``204`` and no page. The page forwards ``code``
        and ``state`` to the callback route with the browser's cookies, which carry fastapi-users' state cookie.

        :returns: Router holding sign-in, registration, verification, reset, OAuth and user routes.
        :rtype: APIRouter
        """
        auth = APIRouter(prefix='/auth', tags=[AUTH_TAG])
        auth.add_api_route('/providers', self.list_providers, methods=['GET'], name='list_providers')
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
                redirect_url=OAUTH_CALLBACK_PAGE.format(public_url=self.public_url.rstrip('/'), name=client.name),
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
