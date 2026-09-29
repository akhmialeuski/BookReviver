"""Who is calling: the dependency every protected route uses to get the acting account.

Routers declare ``ActorDep`` when they are imported, but fastapi-users builds its current-user dependency only once
the application has read its settings. So ``current_actor`` depends on the placeholder ``signed_in_user``, which the
application factory binds to fastapi-users' current active verified user. Tests of other features override
``current_actor`` itself through ``app.dependency_overrides``.
"""

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends

from bookreviver.api.problems import Unauthorized
from bookreviver.domain.entities import Actor
from bookreviver.domain.ids import AccountId

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


def current_actor(user: Annotated[UserProtocol[UUID], Depends(signed_in_user)]) -> Actor:
    """Return the signed-in account as the actor the services work for.

    :param user: The signed-in, active, verified user that ``signed_in_user`` resolves to.
    :type user: UserProtocol[UUID]
    :returns: The actor carrying the identifier of that user.
    :rtype: Actor
    """
    return Actor(account_id=AccountId(user.id))


ActorDep = Annotated[Actor, Depends(current_actor)]
