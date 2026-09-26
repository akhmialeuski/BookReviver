"""Who is calling: the dependency every protected route uses to get the acting account.

The accounts feature replaces the body of ``current_actor`` with the fastapi-users current-user dependency. Tests of
other features override it through ``app.dependency_overrides``.
"""

from typing import Annotated

from fastapi import Depends

from bookreviver.api.problems import Unauthorized
from bookreviver.domain.entities import Actor

SIGN_IN_REQUIRED: str = 'Sign in to continue.'


def current_actor() -> Actor:
    """Return the signed-in account.

    :raises Unauthorized: Until the accounts feature provides sign-in.
    """
    raise Unauthorized(SIGN_IN_REQUIRED)


ActorDep = Annotated[Actor, Depends(current_actor)]
