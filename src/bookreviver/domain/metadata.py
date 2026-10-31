"""JSON-compatible metadata as the domain passes it on: the facts of a file and the data of a result."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping


# JSON-compatible metadata as read from a source file
type MetadataMap = Mapping[str, Any]
