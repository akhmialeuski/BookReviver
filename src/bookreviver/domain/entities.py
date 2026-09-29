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
    """The signed-in account on whose behalf a use case runs.

    :ivar account_id: Identifier of the signed-in account.
    """

    account_id: AccountId


@frozen(kw_only=True)
class Project:
    """One book being digitised, owned by one account.

    :ivar id: Identifier of the project.
    :ivar owner_id: Account owning the project, the only one that may read or change it.
    :ivar details: Bibliographic description of the book.
    :ivar source: The upload the book was imported from, or None before the first import.
    :ivar created_at: When the project was created.
    :ivar updated_at: When the project was last changed, which orders the owner's project list.
    """

    id: ProjectId
    owner_id: AccountId
    details: BookDetails
    source: SourceSummary | None = None
    created_at: datetime
    updated_at: datetime

    def is_owned_by(self, actor: Actor) -> bool:
        """Return whether the actor owns this project.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :returns: True when the actor's account is the project's owner.
        :rtype: bool
        """
        return self.owner_id == actor.account_id


@frozen(kw_only=True)
class ProjectOverview:
    """A project as shown in a list, with the size of its book.

    :ivar project: The listed project.
    :ivar page_count: Number of pages imported into the project.
    """

    project: Project
    page_count: int


@frozen(kw_only=True)
class Page:
    """One page of an imported source.

    :ivar project_id: Project owning the page.
    :ivar index: Position of the page in the book, starting at 0, which together with the project identifies it.
    :ivar facts: Technical facts of the page as found in the source.
    :ivar assets: Readiness and version of the page's derived files.
    """

    project_id: ProjectId
    index: int
    facts: PageFacts
    assets: PageAssets = field(factory=PageAssets)

    def asset_key(self, asset: PageAsset) -> StorageKey:
        """Return the storage key of a derived file, unique per asset version so it can be cached forever.

        :param asset: Derived file of the page, such as the full image, the thumbnail or the tile pyramid.
        :type asset: PageAsset
        :returns: Key of the form ``projects/<id>/pages/<index>/v<version>/<asset>``.
        :rtype: StorageKey
        """
        return StorageKey(f'projects/{self.project_id}/pages/{self.index}/v{self.assets.version}/{asset}')


@frozen(kw_only=True)
class Job:
    """A background job and how far it has come.

    :ivar id: Identifier of the job.
    :ivar project_id: Project the job works on.
    :ivar kind: What the job does, which also selects its worker pool.
    :ivar state: Where the job is in its life cycle.
    :ivar progress: How many of its steps are done.
    :ivar error: Why the job failed, shown to the user, or empty.
    :ivar created_at: When the job was recorded.
    :ivar started_at: When a worker started the job, or None while it is queued.
    :ivar finished_at: When the job reached a final state, or None before.
    """

    id: JobId
    project_id: ProjectId
    kind: JobKind
    state: JobState = JobState.QUEUED
    progress: Progress = field(factory=Progress)
    error: str = ''
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
