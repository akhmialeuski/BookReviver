"""Identifiers that the type checkers keep apart.

Every entity is identified by a random UUID the domain assigns, except a page version. A version is identified by a
hash of what produced it, so equal work gets the same identifier and finds its cached result, and that identifier is a
string of hexadecimal digits.
"""

from typing import NewType
from uuid import UUID

AccountId = NewType('AccountId', UUID)
ProjectId = NewType('ProjectId', UUID)
SourceId = NewType('SourceId', UUID)
ScanId = NewType('ScanId', UUID)
PageId = NewType('PageId', UUID)
PageVersionId = NewType('PageVersionId', str)
JobId = NewType('JobId', UUID)
StorageKey = NewType('StorageKey', str)
