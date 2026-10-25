"""Who is calling: the placeholder for the signed-in user that the access of the API reads.

Routers are imported before the application has read its settings, and fastapi-users builds its current-user
dependency only after that. So ``access`` depends on the placeholder ``signed_in_user``, which the application factory
binds to fastapi-users' current active verified user. Tests override ``signed_in_user`` through
``app.dependency_overrides`` to sign a stub user in.
"""

from typing import TYPE_CHECKING

from bookreviver.api.problems import Unauthorized

if TYPE_CHECKING:
    from uuid import UUID

    from fastapi_users.models import UserProtocol

SIGN_IN_REQUIRED: str = 'Sign in to continue.'


def signed_in_user() -> UserProtocol[UUID]:
    """Return the signed-in, active, verified user; the application factory binds this to fastapi-users.

    :returns: The signed-in user; never returned by the placeholder itself, only by the dependency bound in its place.
    :rtype: UserProtocol[UUID]
    :raises Unauthorized: While nothing is bound, as in an application built without accounts.
    """
    raise Unauthorized(SIGN_IN_REQUIRED)
