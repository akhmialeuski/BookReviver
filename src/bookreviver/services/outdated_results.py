"""The stage records whose result was made by a processor that has since been replaced by a newer version.

A page version stores the processor that made it, by key and version, and the plugin that provides the processor may be
installed again in a newer version, because the algorithm changed what it makes. The records of the stages built from
the old results stay fresh, since nothing in the data of a page changed, so the workspace would show the old result
and a run would never compute it again. This class finds those records and marks them stale, which is the mark the
interface already shows with the old result and a run already recomputes.

A record is outdated when any version in the chain of its current version was made by a processor that the catalogue
has with another version. The chain is walked back through the versions each one read, across the stages, so a stage
built on an outdated version of an earlier stage is caught as well. A processor the catalogue does not have is never
taken as outdated, since a plugin that is not installed says nothing about its version, and a record that is stale or
failed already is left as it is.

The records of every book of the library are checked, once, when the application starts. No event is published, because
nothing is listening yet: a browser reads the stages of a book when it opens the book, after the application has
started.
"""

import logging
from typing import TYPE_CHECKING

from attrs import evolve

from bookreviver.domain.enums import StageState

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from bookreviver.domain.entities import BookRecord, PageStage, PageVersion
    from bookreviver.domain.ids import PageVersionId
    from bookreviver.ports.persistence import BookRepositories
    from bookreviver.ports.processing import ProcessorCatalog
    from bookreviver.ports.runtime import Clock
    from bookreviver.services.book import Book
    from bookreviver.services.books import BooksService

logger = logging.getLogger(__name__)


class OutdatedResults:
    """Marks the fresh stage records of all books stale when a processor that made their result has a new version."""

    def __init__(self, *, books: BooksService, catalogue: ProcessorCatalog, clock: Clock) -> None:
        """Work over the ports of one unit of work.

        :param books: Books service that opens each listed book, whose transactions read the records and mark them.
        :type books: BooksService
        :param catalogue: The processors the application can run, with the versions that are installed.
        :type catalogue: ProcessorCatalog
        :param clock: Clock stamping the records that change.
        :type clock: Clock
        """
        self._books = books
        self._catalogue = catalogue
        self._clock = clock

    async def mark_stale(self) -> list[PageStage]:
        """Mark every fresh record whose result a replaced version of a processor made stale, one book at a time.

        The versions of all the chains of a book are read a level at a time, each distinct version once however many
        records, pages and stages lead to it, so the number of queries follows the depth of the chains, and the number
        of rows read the number of distinct versions. A version is judged once, and a chain that meets a version judged
        already takes its verdict. The reading of a book is one transaction that only reads, so it takes no lock. The
        records are then written by one ``Book.change`` for the book, which reads each record again and marks it only
        if it is still fresh and still stands on the same version.

        :returns: The records that became stale.
        :rtype: list[PageStage]
        """
        installed = {spec.key: spec.version for spec in self._catalogue.specs()}

        def find(repos: BookRepositories) -> list[PageStage]:
            """Find the fresh records of the book that stand on a replaced version.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: The outdated records of the book.
            :rtype: list[PageStage]
            """
            records = repos.page_stages.list_fresh()
            heads = {record.head_version_id for record in records if record.head_version_id is not None}
            verdicts = VersionChains(repos, installed, heads).verdicts()
            return [
                record
                for record in records
                if record.head_version_id is not None and verdicts.get(record.head_version_id)
            ]

        stale: list[PageStage] = []
        for book in await self._books.listed():
            if outdated := await book.read(find):
                stale.extend(await self._mark_book(book, outdated))
        logger.info('Marked %d stage records stale because a processor of their result has a new version.', len(stale))
        return stale

    async def _mark_book(self, book: Book, outdated: Sequence[PageStage]) -> list[PageStage]:
        """Mark the outdated records of one book stale, in a ``Book.change`` of its own.

        The records of the pages are read again in one query of the transaction, since a request may have marked them,
        run them or deleted them since the outdated ones were found. A record that is not fresh any longer, or stands
        on another version, is left as it is, and the others are written by one statement. The method opens its
        transaction, so it is called only outside one.

        :param book: The open book owning the records.
        :type book: Book
        :param outdated: The records found outdated, which belong to the pages of the book.
        :type outdated: Sequence[PageStage]
        :returns: The records that became stale.
        :rtype: list[PageStage]
        """
        moment = self._clock.now()

        def work(repos: BookRepositories, _book_record: BookRecord) -> list[PageStage]:
            """Mark the records that are still fresh on the same version.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it, which the marking does not read.
            :type _book_record: BookRecord
            :returns: The records that became stale.
            :rtype: list[PageStage]
            """
            found_on = {record.key: record.head_version_id for record in outdated}
            current = repos.page_stages.list_for_pages({record.page_id for record in outdated})
            stale = [
                evolve(record, state=StageState.STALE, updated_at=moment)
                for record in current
                if record.key in found_on
                and record.state is StageState.FRESH
                and record.head_version_id == found_on[record.key]
            ]
            return list(repos.page_stages.save_many(stale))

        return await book.change(work)


class VersionChains:
    """The chains of versions that the fresh records of one book stand on, and which of them a replaced processor made.

    :ivar versions: Every version of the chains that was read, by identifier.
    :ivar replaced: The versions made by a processor that the catalogue now has in another version.
    """

    def __init__(self, repos: BookRepositories, installed: Mapping[str, str], heads: set[PageVersionId]) -> None:
        """Read the chains of the heads a level at a time, each distinct version once.

        :param repos: Repositories of the transaction that reads the book.
        :type repos: BookRepositories
        :param installed: The version of each processor the catalogue has, by key.
        :type installed: Mapping[str, str]
        :param heads: The current versions of the fresh records.
        :type heads: set[PageVersionId]
        """
        self._heads = heads
        self.versions: dict[PageVersionId, PageVersion] = {}
        self.replaced: set[PageVersionId] = set()
        asked: set[PageVersionId] = set()
        pending = set(heads)
        while pending:
            asked |= pending
            for read in repos.page_versions.list_by_ids(pending):
                self.versions[read.id] = read
                if installed.get(read.processor.key, read.processor.version) != read.processor.version:
                    self.replaced.add(read.id)
            # The chain of a replaced version is not followed, since its stage is outdated whatever it read
            following = {
                version.input_id
                for version_id in pending - self.replaced
                if (version := self.versions.get(version_id)) is not None and version.input_id is not None
            }
            pending = following - asked

    def verdicts(self) -> dict[PageVersionId, bool]:
        """Judge each version of the chains once: outdated when it or a version before it was made by a replaced one.

        A chain that meets a version judged already takes its verdict.

        :returns: Whether each version that was walked is outdated.
        :rtype: dict[PageVersionId, bool]
        """
        verdicts: dict[PageVersionId, bool] = {}
        for head in self._heads:
            chain: list[PageVersionId] = []
            current: PageVersionId | None = head
            outdated = False
            while current is not None and current in self.versions and current not in verdicts:
                chain.append(current)
                if current in self.replaced:
                    outdated = True
                    break
                current = self.versions[current].input_id
            if current is not None and current in verdicts:
                outdated = verdicts[current]
            verdicts.update(dict.fromkeys(chain, outdated))
        return verdicts
