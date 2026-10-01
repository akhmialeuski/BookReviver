"""Schemas of the account routes: fastapi-users' user schemas on the shared request and response bases."""

from uuid import UUID

from fastapi_users import schemas
from pydantic import ConfigDict, field_validator

from bookreviver.api.schemas.base import RequestModel, ResponseModel

EMAIL_CHANGE_REFUSED: str = 'Changing the email address is not offered.'


class SignInProvider(ResponseModel):
    """A social sign-in provider the server offers, which says nothing about how it is configured.

    :ivar name: Name in the provider's addresses, ``/auth/{name}/authorize`` and the page ``/auth/{name}/callback``.
    :ivar label: Name the interface shows on the sign-in button.
    """

    name: str
    label: str


class AccountRead(ResponseModel, schemas.BaseUser[UUID]):
    """The signed-in account."""


class AccountCreate(RequestModel, schemas.BaseUserCreate):
    """Registration with an email address and a password; the account starts unverified."""

    # A password is kept exactly as typed, as the login form and the reset body that fastapi-users reads keep it
    model_config = ConfigDict(str_strip_whitespace=False)


class AccountUpdate(RequestModel, schemas.BaseUserUpdate):
    """A change to the signed-in account: its password."""

    model_config = ConfigDict(str_strip_whitespace=False)

    @field_validator('email')
    @classmethod
    def refuse_email_change(cls, email: str | None) -> None:
        """Refuse a new address, since answering "address taken" would tell which addresses are registered.

        :param email: New address of the request, or ``None`` when the request leaves it alone.
        :type email: str | None
        :raises ValueError: If an address is given; FastAPI reports it as a validation problem.
        """
        if email is not None:
            raise ValueError(EMAIL_CHANGE_REFUSED)
