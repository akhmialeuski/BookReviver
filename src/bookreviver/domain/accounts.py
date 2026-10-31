"""The account a use case acts for, and the mail an account is sent."""

from typing import TYPE_CHECKING

from attrs import frozen

if TYPE_CHECKING:
    from bookreviver.domain.ids import AccountId


@frozen(kw_only=True)
class MailMessage:
    """A plain-text message to one recipient.

    :ivar to: Email address of the recipient.
    :ivar subject: Subject line.
    :ivar body: Plain-text body.
    """

    to: str
    subject: str
    body: str


@frozen(kw_only=True)
class Actor:
    """The signed-in account on whose behalf a use case runs.

    :ivar account_id: Identifier of the signed-in account.
    """

    account_id: AccountId
