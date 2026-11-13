"""Cutting a scan of an import into its images and the base version of each of its pages.

A scan is stored by its import without images, and ``ScanCutter`` writes them: the four renditions of the scan, and the
base version of every page cut from it, ``split.none``, which holds the page's own copy of the scan's ``full`` image and
the renditions cut from that copy, so the page stands on its own once its scan is deleted. The base version becomes the
current version of the page split in the record of the page. The cutter keeps no state, and the container builds it
once.

The files are written outside any transaction, and the scan and its versions are marked ready together, in one
``Book.change``, which reads the scan and its pages again first.
"""

import logging
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING

from attrs import evolve

from bookreviver.domain.images import Renditions
from bookreviver.domain.keys import BookKeys
from bookreviver.domain.stages import PageStageKey
from bookreviver.domain.versions import VersionState
from bookreviver.services.steps import StepRun

if TYPE_CHECKING:
    from bookreviver.core.book import Book
    from bookreviver.domain.books import BookRecord
    from bookreviver.domain.pages import Page
    from bookreviver.domain.sources import Scan, SourceKind
    from bookreviver.ports.imaging import PageRasterizer
    from bookreviver.ports.persistence import BookRepositories
    from bookreviver.services.base_versions import BaseVersions
    from bookreviver.services.stage_records import StageRecords
    from bookreviver.services.steps import StepRunner

logger = logging.getLogger(__name__)


class ScanCutter:
    """Cuts the images of a scan and the base version of its pages.

    :ivar rasterizer: Port writing one scan as an image.
    :ivar base_versions: Builder of the base version of a page, which also cuts the files of a scan.
    :ivar runner: Runner of the processor that makes the base version of a page from its scan.
    :ivar records: Writer of the stage records, which makes the base version of a page current.
    """

    def __init__(
        self, *, rasterizer: PageRasterizer, base_versions: BaseVersions, runner: StepRunner, records: StageRecords
    ) -> None:
        """Cut scans through the imaging ports of the application.

        :param rasterizer: Port writing one scan as an image.
        :type rasterizer: PageRasterizer
        :param base_versions: Builder of the base version of a page, which also cuts the files of a scan.
        :type base_versions: BaseVersions
        :param runner: Runner of the processor that makes the base version of a page from its scan.
        :type runner: StepRunner
        :param records: Writer of the stage records.
        :type records: StageRecords
        """
        self.rasterizer = rasterizer
        self.base_versions = base_versions
        self.runner = runner
        self.records = records

    async def cut(self, book: Book, kind: SourceKind, scan: Scan) -> bool:
        """Write the four renditions of a scan and the base version of its pages, then mark the scan ready.

        The format of ``full`` is chosen here, from the colour of the scan and the image policy of the book as it is
        now, and recorded with the scan, so a later change of the policy changes no stored path. The base version of a
        page copies that format along with the image. What an earlier attempt left in the directories of the scan and
        of the versions is removed first, since a stored file is never replaced. The files are written outside any
        transaction, and the scan and its versions are marked ready together, in one ``Book.change``. That change reads
        the scan and its pages again. A scan that is gone or ready already, or whose pages are not the ones the
        versions were made for, is left as it is, and its files are removed.

        :param book: The open book holding the scan.
        :type book: Book
        :param kind: Kind of the source holding the scan.
        :type kind: SourceKind
        :param scan: The scan to cut, whose renditions are not ready.
        :type scan: Scan
        :returns: Whether the scan and its versions were stored, which they are not when the book changed meanwhile.
        :rtype: bool
        """

        def start(repos: BookRepositories) -> tuple[BookRecord, list[Page]]:
            """Read the record of the book and the pages of the scan, which takes no place in the queue of the writers.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: The record of the book, whose image policy chooses the format of ``full``, and the pages cut from
                      the scan.
            :rtype: tuple[BookRecord, list[Page]]
            """
            return repos.book_record.get(), list(repos.pages.list_for_scan(scan.id))

        book_record, pages = await book.read(start)
        full = book_record.image_policy.full_format(scan.facts.color_mode)
        ready = evolve(scan, renditions=Renditions(ready=True, version=scan.renditions.version, full=full))
        of_scan = partial(BookKeys.scan_rendition, ready)
        await book.assets.delete_prefix(BookKeys.scan_directory(scan))
        async with (
            book.sources.source_files(scan.source_id) as files,
            book.assets.writable(of_scan(full)) as target,
        ):
            await self.rasterizer.extract(kind, files, scan.number, target, full=full)
        await self.base_versions.derive(of_scan, assets=book.assets, full=full)
        versions = []
        key = self.base_versions.scan_copy.key
        run = StepRun(processor_key=key, params={}, input_data=ready.facts.as_data(), image=of_scan(full))
        base = self.base_versions
        for page in pages:
            version = base.split_none(page=page, scan=ready, state=VersionState.READY, moment=datetime.now(UTC))
            async with self.runner.execute(book, run) as result:
                versions.append(
                    await self.runner.store(
                        book, version, result.outputs[0], policy=book_record.image_policy, tiles=True
                    )
                )

        def finish(repos: BookRepositories, _book_record: BookRecord) -> bool:
            """Store the versions and mark the scan ready, unless the book changed while the files were cut.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it, which the check does not need.
            :type _book_record: BookRecord
            :returns: Whether the scan and its pages were still the ones the versions were made for.
            :rtype: bool
            """
            # The scan and its pages are read again, since the book may have changed while the files were cut
            unready = {unready_scan.id for unready_scan in repos.scans.list_unready()}
            current = {page.id for page in repos.pages.list_for_scan(scan.id)}
            if scan.id not in unready or current != {page.id for page in pages}:
                return False
            repos.page_versions.add_many(versions)
            for page, version in zip(pages, versions, strict=True):
                self.records.set_head(
                    repos, PageStageKey(page.id, version.stage), head_version_id=version.id, recipe_id=None
                )
            repos.scans.update(ready)
            return True

        fits = await book.change(finish)
        if not fits:
            # The scan stays unready for the next run to cut, and the files made for it are of no version or scan
            logger.warning('Scan %s was changed or deleted while its images were cut, so they were not stored', scan.id)
            await book.assets.delete_prefix(BookKeys.scan_directory(scan))
            for version in versions:
                await book.assets.delete_prefix(BookKeys.version_directory(version))
            return False
        return True
