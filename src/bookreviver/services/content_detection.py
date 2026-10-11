"""Detecting the content of the pages: what each page shows, written into the page as a proposal the user may change.

The job reads the preview of the base image of a page through the processor ``pages.content``, which finds the pictures
of the page and their colour, and writes the answer into the page. A job that names no pages goes over the pages of the
book that have an image and no content type yet, so a page that was detected, or that the user set by hand, is not read
again, and the covers, the endpapers and the blank pages are left out, since a page the user gave such a role to is not
a page of text or of pictures to be told apart. A job that names pages goes over those and only those, whatever they
have and whatever their kind, and a page the user had set by hand is given back to the detection.

Detection never writes over a change it did not read. A page the user changed while the job was working on it is read
again, and a type the user set meanwhile stays. A page whose kind changed has its stages after the page order marked
stale, since the recipe that made their versions is not the one the page now meets.
A page that cannot be read fails for itself and the job goes on.
"""

import logging
from itertools import batched
from typing import TYPE_CHECKING, ClassVar

from attrs import evolve

from bookreviver.domain.enums import (
    ContentType,
    PageChange,
    PageKind,
    PageOrigin,
    Rendition,
    VersionData,
    VersionScale,
    VersionState,
)
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.events import PagesChanged
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import ContentDetection, SliceRequest
from bookreviver.services.steps import StepRun

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.entities import Job, Page, PageVersion
    from bookreviver.domain.ids import PageId, ProjectId
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.services.job_runs import JobTracker
    from bookreviver.services.stage_records import StageRecords
    from bookreviver.services.stage_runs import StageRuntime

PROBE_KEY: str = 'pages.content'
logger = logging.getLogger(__name__)


class ContentDetector:
    """Detects the content of the pages of one job and writes it into them."""

    # The kinds of page a job that names no pages goes over: the others are a role the user gave the page
    KINDS: ClassVar[frozenset[PageKind]] = frozenset(
        {PageKind.TEXT, PageKind.TITLE, PageKind.PLATE, PageKind.FRONTISPIECE, PageKind.OTHER}
    )
    # How many pages are read from the database at once
    WINDOW: ClassVar[int] = 500

    def __init__(self, *, uow: UnitOfWork, runtime: StageRuntime, records: StageRecords, tracker: JobTracker) -> None:
        """Work over the ports of one job.

        :param uow: Unit of work whose ``change_book`` block holds the write of each page.
        :type uow: UnitOfWork
        :param runtime: The runner of the processor that finds the content of an image, the publisher and the clock.
        :type runtime: StageRuntime
        :param records: Writer of the stage records, which marks the stages of a page stale.
        :type records: StageRecords
        :param tracker: The life of the job on the worker, which records its progress and learns of its cancellation.
        :type tracker: JobTracker
        """
        self._uow = uow
        self._runner = runtime.runner
        self._catalogue = runtime.catalogue
        self._publisher = runtime.publisher
        self._clock = runtime.clock
        self._records = records
        self._tracker = tracker

    async def run(self, job: Job) -> tuple[int, int] | None:
        """Detect the content of the pages of a running job, recording the progress as it goes.

        :param job: The running job.
        :type job: Job
        :returns: How many pages were read and how many could not be, none of them when the processor is not installed,
                  or None when the job was cancelled.
        :rtype: tuple[int, int] | None
        :raises InvalidParametersError: If the parameters of the job are not those of a ``detect-content`` job.
        :raises NotFoundError: If the job names a page that is gone.
        """
        detection = ContentDetection.from_map(job.params)
        # A machine without OpenCV offers no such processor, and then the kind of a page is all that says what it shows
        if all(spec.key != PROBE_KEY for spec in self._catalogue.specs()):
            return 0, 0
        pages = await self._pages(job.project_id, detection)
        changed: list[PageId] = []
        read = failed = 0
        for window in batched(pages, self.WINDOW, strict=False):
            bases = await self._bases(window)
            for page in window:
                if (saved := await self._tracker.advance(job, done=read + failed, total=len(pages))) is None:
                    return None
                job = saved
                if (base := bases.get(page.id)) is None:
                    continue
                try:
                    content = await self._probe(job.project_id, base)
                except Exception:
                    logger.exception('The content of page %s could not be detected', page.id)
                    failed += 1
                    continue
                read += 1
                if await self._write(job.project_id, page.id, content, forced=detection.page_ids is not None):
                    changed.append(page.id)
        if changed:
            event = PagesChanged(project_id=job.project_id, page_ids=changed, change=PageChange.EDITED)
            await self._publisher.publish(event)
        return read, failed

    async def _pages(self, project_id: ProjectId, detection: ContentDetection) -> Sequence[Page]:
        """Choose the pages a job goes over.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param detection: What the job was asked to detect.
        :type detection: ContentDetection
        :returns: The pages in book order that have an image to read: the ones named, or those not detected yet.
        :rtype: Sequence[Page]
        :raises NotFoundError: If the job names a page that is gone.
        """
        if detection.page_ids is not None:
            named = await self._uow.pages.list_by_ids(project_id, detection.page_ids)
            return [page for page in named if self._readable(page)]
        pages: list[Page] = []
        while True:
            window = await self._uow.pages.list_for_project(
                project_id, SliceRequest(offset=len(pages), limit=self.WINDOW)
            )
            pages.extend(window.items)
            if len(pages) >= window.total or not window.items:
                break
        return [
            page for page in pages if self._readable(page) and page.content_type is None and page.kind in self.KINDS
        ]

    @staticmethod
    def _readable(page: Page) -> bool:
        """Tell whether a page has an image of a scan to read, which a placeholder and a drawn leaf have not.

        :param page: The page.
        :type page: Page
        :returns: True for a page cut from a scan whose image is the scan.
        :rtype: bool
        """
        return page.origin is PageOrigin.SCAN and not page.is_leaf

    async def _bases(self, pages: Sequence[Page]) -> dict[PageId, PageVersion]:
        """Read the base version of each page whose image is ready, which is the image a page starts from.

        :param pages: Pages whose base versions are read in one query.
        :type pages: Sequence[Page]
        :returns: The latest ready base version of each page that has one.
        :rtype: dict[PageId, PageVersion]
        """
        versions = await self._uow.page_versions.list_base_versions([page.id for page in pages])
        # The versions come earliest first, so the latest of a page that was cut again is the one that stays
        return {
            version.page_id: version
            for version in versions
            if version.state is VersionState.READY and version.renditions is not None and version.renditions.ready
        }

    async def _probe(self, project_id: ProjectId, base: PageVersion) -> ContentType:
        """Tell what the image of a base version shows, by the processor, on its preview.

        :param project_id: Project owning the page.
        :type project_id: ProjectId
        :param base: The ready base version of the page.
        :type base: PageVersion
        :returns: The content type the processor found.
        :rtype: ContentType
        :raises NotFoundError: If the processor is not installed, or a file of the version is not stored.
        :raises DomainError: If the image cannot be read.
        """
        run = StepRun(
            processor_key=PROBE_KEY,
            params={},
            input_data=base.data,
            image=ProjectKeys(project_id).version_rendition(base, Rendition.PREVIEW),
            scale=VersionScale.PREVIEW,
        )
        async with self._runner.execute(run) as result:
            [output] = result.outputs
        return ContentType(output.data[VersionData.CONTENT_TYPE])

    async def _write(self, project_id: ProjectId, page_id: PageId, content: ContentType, *, forced: bool) -> bool:
        """Write what was detected into the page in one short ``change_book`` block, which reads the page again first.

        Opens its own block, so it is called outside any block. The page is read inside the block, so a change of
        another request that committed after the probe stays. Nothing is written when the page is gone, when the user
        set its content by hand and did not ask for the page by name, or when the page already shows the content.

        :param project_id: Project owning the page.
        :type project_id: ProjectId
        :param page_id: Page that was read.
        :type page_id: PageId
        :param content: What the page was found to show.
        :type content: ContentType
        :param forced: Whether the user asked for this page by name, which gives a page set by hand back to detection.
        :type forced: bool
        :returns: True when the page was written.
        :rtype: bool
        """
        async with self._uow.change_book(project_id):
            try:
                before = await self._uow.pages.get(page_id)
            except NotFoundError:
                return False
            if before.content_by_hand and not forced:
                return False
            after = evolve(before, content_type=content, content_by_hand=False, updated_at=self._clock.now())
            if after == evolve(before, updated_at=after.updated_at):
                return False
            await self._uow.pages.update(after)
            shown = before.recipe_kind is not after.recipe_kind
            stale = await self._records.mark_content_stale(page_id) if shown else []
        await self._records.announce(project_id, stale)
        return True
