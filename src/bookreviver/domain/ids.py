"""Identifiers that the type checkers keep apart."""

from typing import NewType
from uuid import UUID

AccountId = NewType('AccountId', UUID)
ProjectId = NewType('ProjectId', UUID)
JobId = NewType('JobId', UUID)
StorageKey = NewType('StorageKey', str)
