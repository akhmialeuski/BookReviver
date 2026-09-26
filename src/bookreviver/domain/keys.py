"""Layout of storage keys: every stored file of a project lives under a prefix derived from its identifier."""

from typing import ClassVar, Self
from uuid import UUID

from attrs import frozen

from bookreviver.domain.ids import ProjectId, StorageKey


@frozen
class ProjectKeys:
    """The part of the storage key space that belongs to one project."""

    project_id: ProjectId

    # First segment of every project key, as ``Page.asset_key`` writes it
    ROOT: ClassVar[str] = 'projects'
    SEPARATOR: ClassVar[str] = '/'
    # A key made of the root, the project and at least one name inside the project
    MIN_SEGMENTS: ClassVar[int] = 3
    # Segments that would address the project itself or climb out of it
    UNSAFE_SEGMENTS: ClassVar[frozenset[str]] = frozenset({'', '.', '..'})

    @property
    def prefix(self) -> StorageKey:
        """The prefix shared by every key of the project, ending with the separator."""
        return StorageKey(f'{self.ROOT}{self.SEPARATOR}{self.project_id}{self.SEPARATOR}')

    @classmethod
    def owning(cls, key: StorageKey) -> Self | None:
        """Return the key space that ``key`` belongs to, or None when it is not a well-formed project key."""
        segments = key.split(cls.SEPARATOR)
        if len(segments) < cls.MIN_SEGMENTS or segments[0] != cls.ROOT or cls.UNSAFE_SEGMENTS.intersection(segments):
            return None
        try:
            project_id = UUID(segments[1])
        except ValueError:
            return None
        return cls(ProjectId(project_id))
