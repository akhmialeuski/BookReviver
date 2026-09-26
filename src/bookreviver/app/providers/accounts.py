"""Provider of the accounts feature: users, sessions, OAuth clients and mail."""

from dishka import Provider


class AccountsProvider(Provider):
    """Builds the account adapters and the fastapi-users wiring."""
