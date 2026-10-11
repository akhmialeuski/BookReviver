"""Keeping the printed numbers of the pages of a book in step with its pagination sections.

The label of a page is computed from the sections by ``Pagination``, except for a label written by hand, which is an
exception that no recompute changes. A use case that adds, deletes or moves pages, or changes a section, calls
``recompute`` inside its ``change_book`` block, so the numbers change in the transaction of the change itself, and only
the pages whose label changes are written. The pages renumbered are announced after the block, as one ``PagesChanged``
of kind ``edited``.

A section starts at a page and so does not survive the deletion of that page. ``recompute`` is told which pages are
about to leave, and hands every section that starts at one of them to the next page that stays, or removes it when no
page follows.
"""

from typing import TYPE_CHECKING

from attrs import evolve

from bookreviver.domain.enums import PageChange
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.events import PagesChanged
from bookreviver.domain.pagination import Pagination
from bookreviver.domain.values import SliceRequest
from bookreviver.services.projects import PAGE_WINDOW

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from bookreviver.domain.entities import Page, PaginationSection
    from bookreviver.domain.ids import PageId, ProjectId
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.runtime import Clock, EventPublisher


class PageLabels:
    """Recomputes the labels of the pages of a book in the block of its caller, and announces them after it."""

    def __init__(self, *, uow: UnitOfWork, publisher: EventPublisher, clock: Clock) -> None:
        """Write labels through the unit of work of the use case.

        :param uow: Unit of work whose ``change_book`` block ends the use case.
        :type uow: UnitOfWork
        :param publisher: Publisher of the events the browser follows.
        :type publisher: EventPublisher
        :param clock: Clock stamping the pages and sections it writes.
        :type clock: Clock
        """
        self._uow = uow
        self._publisher = publisher
        self._clock = clock
        self._renumbered: list[PageId] = []

    async def book(self, project_id: ProjectId) -> list[Page]:
        """Read every page of a project in book order, the pages kept out of the book included.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :returns: The pages of the book.
        :rtype: list[Page]
        """
        pages: list[Page] = []
        while True:
            window = await self._uow.pages.list_for_project(
                project_id, SliceRequest(offset=len(pages), limit=PAGE_WINDOW)
            )
            pages.extend(window.items)
            if len(pages) >= window.total or not window.items:
                return pages

    async def recompute(
        self,
        project_id: ProjectId,
        *,
        leaving: Collection[PageId] = (),
        unpin: Collection[PageId] = (),
        force: bool = False,
    ) -> list[Page]:
        """Write the labels the sections give the pages of a project, in the ``change_book`` block of the caller.

        It writes inside the block of its caller and opens none, so it is called only inside a block. A book without a
        section has no computed label to keep, so the call stops after reading the sections, unless ``force`` says a
        section was just removed.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param leaving: Pages the caller is about to delete. They take no part in the count, and the sections that
                        start at them are handed to the next page that stays.
        :type leaving: Collection[PageId]
        :param unpin: Pages whose hand-written label is dropped, which gives their number back to the sections.
        :type unpin: Collection[PageId]
        :param force: Whether to recompute although the book has no section.
        :type force: bool
        :returns: The pages written, with their new labels, which ``announce`` reports after the block.
        :rtype: list[Page]
        :raises ConflictError: If a section cannot write a number, such as 4000 in Roman numerals.
        """
        sections = await self._uow.pagination_sections.list_for_project(project_id)
        if not sections and not force:
            return []
        pages = await self.book(project_id)
        sections = await self._hand_over(sections, pages, set(leaving))
        original = {page.id: page for page in pages}
        staying = [
            evolve(page, label_manual=False) if page.id in unpin else page for page in pages if page.id not in leaving
        ]
        try:
            labels = Pagination(sections, staying).labels()
        except ValueError as error:
            raise ConflictError(str(error)) from error
        moment = self._clock.now()
        changed = [
            evolve(candidate, updated_at=moment)
            for page in staying
            if (candidate := evolve(page, label=labels[page.id])) != original[page.id]
        ]
        if changed:
            await self._uow.pages.update_many(changed)
            self._renumbered.extend(page.id for page in changed)
        return changed

    async def announce(self, project_id: ProjectId) -> None:
        """Tell the browser which pages were renumbered since the last announcement, after the caller's block ended.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        """
        if not self._renumbered:
            return
        page_ids, self._renumbered = self._renumbered, []
        await self._publisher.publish(
            PagesChanged(project_id=project_id, page_ids=list(dict.fromkeys(page_ids)), change=PageChange.EDITED)
        )

    async def _hand_over(
        self, sections: Sequence[PaginationSection], pages: Sequence[Page], leaving: Collection[PageId]
    ) -> list[PaginationSection]:
        """Move the sections that start at a page that leaves to the next page that stays, or remove them.

        A section that would start at a page where a section it clashes with starts is removed, since the later
        section of its flow is the one in force there.

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
                await self._uow.pagination_sections.delete(section.id)
            else:
                settled.append(await self._uow.pagination_sections.update(moved))
        return settled
