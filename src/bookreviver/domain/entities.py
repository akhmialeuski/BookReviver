"""Entities: domain objects with an identity, frozen and changed through ``attrs.evolve``."""

from typing import TYPE_CHECKING

from attrs import field, frozen

from bookreviver.domain.enums import JobState
from bookreviver.domain.ids import StorageKey
from bookreviver.domain.values import PageAssets, Progress

if TYPE_CHECKING:
    from datetime import datetime

    from bookreviver.domain.enums import JobKind, PageAsset
    from bookreviver.domain.ids import AccountId, JobId, ProjectId
    from bookreviver.domain.values import BookDetails, PageFacts, SourceSummary


@frozen(kw_only=True)
class Actor:
    """The signed-in account on whose behalf a use case runs."""

    account_id: AccountId


@frozen(kw_only=True)
class Project:
    """One book being digitised, owned by one account."""

    id: ProjectId
    owner_id: AccountId
    details: BookDetails
    source: SourceSummary | None = None
    created_at: datetime
    updated_at: datetime

    def is_owned_by(self, actor: Actor) -> bool:
        """Whether the actor owns this project."""
        return self.owner_id == actor.account_id


@frozen(kw_only=True)
class ProjectOverview:
    """A project as shown in a list, with the size of its book."""

    project: Project
    page_count: int


@frozen(kw_only=True)
class Page:
    """One page of an imported source."""

    project_id: ProjectId
    index: int
    facts: PageFacts
    assets: PageAssets = field(factory=PageAssets)

    def asset_key(self, asset: PageAsset) -> StorageKey:
        """Return the storage key of a derived file, unique per asset version so it can be cached forever."""
        return StorageKey(f'projects/{self.project_id}/pages/{self.index}/v{self.assets.version}/{asset}')


@frozen(kw_only=True)
class Job:
    """A background job and how far it has come."""

    id: JobId
    project_id: ProjectId
    kind: JobKind
    state: JobState = JobState.QUEUED
    progress: Progress = field(factory=Progress)
    error: str = ''
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
