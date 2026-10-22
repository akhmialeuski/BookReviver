"""Seeding the current version of a stage of a page directly, for the tests that read what a stage reads on a page."""

from typing import TYPE_CHECKING

from attrs import evolve

from bookreviver.domain.enums import StageState, VersionState
from bookreviver.domain.values import Renditions
from tests.helpers.builders import make_page_stage, make_page_version

if TYPE_CHECKING:
    from bookreviver.domain.entities import PageVersion, Project
    from bookreviver.domain.ids import ProjectId
    from bookreviver.domain.values import PageStageKey
    from bookreviver.ports.persistence import BookRepositories
    from tests.helpers.processing import ProcessingKit


async def seed_head(
    kit: ProcessingKit, project_id: ProjectId, key: PageStageKey, *, after: PageVersion, ready: bool = True
) -> PageVersion:
    """Commit a version of a stage that reads another version, and the record of the stage that names it as current.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param project_id: Identifier of the project of the page.
    :type project_id: ProjectId
    :param key: The page and the stage the version and the record belong to.
    :type key: PageStageKey
    :param after: The version the new one reads, which an earlier stage made.
    :type after: PageVersion
    :param ready: Whether the files of the new version are published.
    :type ready: bool
    :returns: The version, which is the current one of the stage.
    :rtype: PageVersion
    """
    version = evolve(
        make_page_version(page_id=key.page_id),
        stage=key.stage,
        input_id=after.id,
        renditions=Renditions(ready=ready),
        state=VersionState.READY,
    )

    def save_head(repos: BookRepositories, _project: Project) -> None:
        """Add the version and save the record of the stage that names it as current.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :param _project: Project of the book, which the work does not need.
        :type _project: Project
        """
        repos.page_versions.add(version)
        repos.page_stages.save(
            make_page_stage(page_id=key.page_id, stage=key.stage, head_version_id=version.id, state=StageState.FRESH)
        )

    await kit.uow().change_book(project_id, save_head)
    return version
