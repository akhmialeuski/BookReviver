"""Keeping the pagination sections of a book valid while its pages change, and telling which pages they renumber.

The label a page shows is computed when the page is read, from the sections by ``Pagination``, except for a manual
label, which no section changes. Nothing computed is stored, so a change of the pages or of the sections writes no
label. A use case that adds, deletes or moves pages, or changes a section, calls ``settle`` in the work of its
``change_book`` with the repositories of that work, which refuses the change when a section can no longer write one of
its numbers. ``settle`` is synchronous and opens no transaction.

A section starts at a page and so does not survive the deletion of that page. ``settle`` is told which pages are about
to leave, and hands every section that starts at one of them to the next page that stays, or removes it when no page
follows.

A change of the pages announces its own pages, and the browser reads the whole book again for it. A change of a
section touches no page, so it watches the labels with ``watching`` and announces the pages whose label changed as one
``PagesChanged`` of kind ``edited``.
"""

from typing import TYPE_CHECKING, Self

from attrs import evolve

from bookreviver.domain.enums import PageChange
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.events import PagesChanged
from bookreviver.domain.pagination import Pagination
from bookreviver.services.projects import book_pages

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence
    from types import TracebackType

    from bookreviver.domain.entities import Page, PaginationSection
    from bookreviver.domain.ids import PageId, ProjectId
    from bookreviver.domain.pagination import Numbering
    from bookreviver.ports.persistence import BookRepositories
    from bookreviver.ports.runtime import Clock, EventPublisher


class PageLabels:
    """Settles the sections of a book in the transaction of its caller, and announces renumbered pages after it."""

    def __init__(self, *, publisher: EventPublisher, clock: Clock) -> None:
        """Stamp the pages and sections with a clock and announce the renumbered pages through a publisher.

        :param publisher: Publisher of the events the browser follows.
        :type publisher: EventPublisher
        :param clock: Clock stamping the pages and sections it writes.
        :type clock: Clock
        """
        self._publisher = publisher
        self._clock = clock

    def settle(
        self, repos: BookRepositories, *, leaving: Collection[PageId] = (), unpin: Collection[PageId] = ()
    ) -> None:
        """Hand on the sections of the pages that leave, and check that every section can number its pages.

        It writes through the repositories of its caller and opens no transaction, so it is called only from the work
        of ``change_book``. A book without a section has nothing to hand on and no number to check.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param leaving: Pages the caller is about to delete. They take no part in the count, and the sections that
                        start at them are handed to the next page that stays.
        :type leaving: Collection[PageId]
        :param unpin: Pages whose manual label is dropped, which gives their number back to the sections.
        :type unpin: Collection[PageId]
        :raises ConflictError: If a section cannot write a number, such as 4000 in Roman numerals.
        """
        if unpin:
            moment = self._clock.now()
            pinned = [page for page in repos.pages.list_by_ids(unpin) if page.manual_label is not None]
            repos.pages.update_many([evolve(page, manual_label=None, updated_at=moment) for page in pinned])
        if not (sections := repos.pagination_sections.list_all()):
            return
        pages = book_pages(repos.pages)
        sections = self._hand_over(repos, sections, pages, set(leaving))
        try:
            Pagination(sections, [page for page in pages if page.id not in leaving]).labels()
        except ValueError as error:
            raise ConflictError(str(error)) from error

    @staticmethod
    def numbering(repos: BookRepositories, pages: Sequence[Page]) -> Numbering:
        """Give some pages of a book the labels they show and the sections that govern them.

        A section numbers the pages from its first page on, so a book with sections is read whole. A book without one
        is not read at all: its pages show their manual labels or none.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param pages: The pages to number, in book order.
        :type pages: Sequence[Page]
        :returns: The numbering of the pages, and of every other page of a book with sections.
        :rtype: Numbering
        """
        sections = repos.pagination_sections.list_all()
        return Pagination(sections, book_pages(repos.pages) if sections else pages).numbering()

    def watching(self, repos: BookRepositories) -> LabelWatch:
        """Watch the labels of the pages of a book for the ones a block of code changes.

        :param repos: Repositories of the transaction the block runs in.
        :type repos: BookRepositories
        :returns: The watch, which is entered with ``with`` around the block, inside the work of the caller.
        :rtype: LabelWatch
        """
        return LabelWatch(repos=repos)

    async def announce(self, project_id: ProjectId, page_ids: Sequence[PageId]) -> None:
        """Tell the browser which pages were renumbered, after the transaction ended.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param page_ids: The pages whose label changed, none for a change that renumbered nothing.
        :type page_ids: Sequence[PageId]
        """
        if page_ids:
            await self._publisher.publish(
                PagesChanged(project_id=project_id, page_ids=list(page_ids), change=PageChange.EDITED)
            )

    def _hand_over(
        self,
        repos: BookRepositories,
        sections: Sequence[PaginationSection],
        pages: Sequence[Page],
        leaving: Collection[PageId],
    ) -> list[PaginationSection]:
        """Move the sections that start at a page that leaves to the next page that stays, or remove them.

        A section that would start at a page where a section it clashes with starts is removed, since the later
        section of its flow is the one in force there.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param sections: Every section of the project.
        :type sections: Sequence[PaginationSection]
        :param pages: Every page of the project in book order.
        :type pages: Sequence[Page]
        :param leaving: Pages about to be deleted.
        :type leaving: Collection[PageId]
        :returns: The sections that remain, with the first pages they have now.
        :rtype: list[PaginationSection]
        """
        position = {page.id: index for index, page in enumerate(pages)}
        settled = [section for section in sections if section.first_page_id not in leaving]
        orphaned = sorted(
            (section for section in sections if section.first_page_id in leaving),
            key=lambda section: position.get(section.first_page_id, 0),
            reverse=True,
        )
        for section in orphaned:
            heir = next(
                (page for page in pages[position.get(section.first_page_id, 0) + 1 :] if page.id not in leaving), None
            )
            moved = None if heir is None else evolve(section, first_page_id=heir.id, updated_at=self._clock.now())
            if moved is None or any(moved.clashes_with(other) for other in settled):
                repos.pagination_sections.delete(section.id)
            else:
                settled.append(repos.pagination_sections.update(moved))
        return settled


class LabelWatch:
    """Finds the pages whose label a block of code changes, by reading the labels of the book before and after it.

    The watch is a synchronous context manager entered inside the work of ``change_book``, and the caller announces
    the pages after the work. A block that raises finds nothing.

    :ivar renumbered: The pages whose label changed, in book order, which are none until a block ends without an error.
    """

    def __init__(self, *, repos: BookRepositories) -> None:
        """Watch the labels of a project through the repositories of the caller's transaction.

        :param repos: Repositories of the transaction the block runs in.
        :type repos: BookRepositories
        """
        self._repos = repos
        self._before: dict[PageId, str] = {}
        self.renumbered: list[PageId] = []

    def __enter__(self) -> Self:
        """Read the labels the pages show before the block.

        :returns: The watch itself.
        :rtype: Self
        """
        self._before = self._labels()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Compare the labels after a block that ended without an error with those before it.

        :param exc_type: Type of the exception the block raised, or None.
        :type exc_type: type[BaseException] | None
        :param exc_value: The exception the block raised, or None.
        :type exc_value: BaseException | None
        :param traceback: Traceback of the exception, or None.
        :type traceback: TracebackType | None
        """
        if exc_type is not None:
            return
        after = self._labels()
        self.renumbered = [page_id for page_id, label in after.items() if self._before.get(page_id) != label]

    def _labels(self) -> dict[PageId, str]:
        """Compute the label every page of the book shows.

        :returns: The label of every page by identifier, in book order.
        :rtype: dict[PageId, str]
        """
        sections = self._repos.pagination_sections.list_all()
        return Pagination(sections, book_pages(self._repos.pages)).labels()
