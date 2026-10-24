"""The mark and the comment a user puts on a result, which is a version of a page.

A result carries one mark, good or bad or none, and one comment. Both are notes of the user on the result, no input of
the step, so changing them leaves the identifier of the version, its files and the stage records alone. Both can be
changed at any time, and the version keeps the current ones only. A change that leaves both as they are writes
nothing. A preview is not a result the user keeps, since a collection deletes it, so it takes no mark.
"""

from typing import TYPE_CHECKING

from attrs import evolve

from bookreviver.domain.enums import VersionScale
from bookreviver.domain.errors import ConflictError, NotFoundError

if TYPE_CHECKING:
    from bookreviver.domain.entities import BookRecord, PageVersion
    from bookreviver.domain.ids import PageId, PageVersionId
    from bookreviver.domain.values import ResultNote
    from bookreviver.ports.persistence import BookRepositories
    from bookreviver.services.book import Book

PREVIEW_TAKES_NO_MARK: str = 'The version {version_id} is a preview, which takes no mark and no comment.'


class ResultMarksService:
    """Sets the marks and comments of the results of the acting account's books."""

    async def set(
        self,
        book: Book,
        page_id: PageId,
        version_id: PageVersionId,
        note: ResultNote,
    ) -> PageVersion:
        """Replace the mark and the comment of a result.

        :param book: The open book.
        :type book: Book
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :param version_id: Identifier of the version.
        :type version_id: PageVersionId
        :param note: The mark and the comment from now on, which replace both.
        :type note: ResultNote
        :returns: The version with its new mark and comment, which is the stored one when nothing changed.
        :rtype: PageVersion
        :raises NotFoundError: If the book has no such page, or the page has no such version.
        :raises ConflictError: If the version is a preview.
        """

        def work(repos: BookRepositories, _book_record: BookRecord) -> PageVersion:
            """Store the new mark and comment of the version.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it, which the lookup of the version reads
                                 again.
            :type _book_record: BookRecord
            :returns: The version with its new mark and comment, which is the stored one when nothing changed.
            :rtype: PageVersion
            :raises NotFoundError: If the book has no such page, or the page has no such version.
            :raises ConflictError: If the version is a preview.
            """
            version = self._version(repos, page_id, version_id)
            if version.scale is not VersionScale.FULL:
                raise ConflictError(PREVIEW_TAKES_NO_MARK.format(version_id=version_id))
            if (note.mark, note.comment) == (version.mark, version.comment):
                return version
            return repos.page_versions.update(evolve(version, mark=note.mark, comment=note.comment))

        return await book.change(work)

    @staticmethod
    def _version(repos: BookRepositories, page_id: PageId, version_id: PageVersionId) -> PageVersion:
        """Return a version of a page of a book, in the transaction of the caller.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :param version_id: Identifier of the version.
        :type version_id: PageVersionId
        :returns: The version.
        :rtype: PageVersion
        :raises NotFoundError: If the book has no such page, or the page has no such version.
        """
        repos.pages.get(page_id)
        version = repos.page_versions.get(version_id)
        if version.page_id != page_id:
            raise NotFoundError(version_id)
        return version
