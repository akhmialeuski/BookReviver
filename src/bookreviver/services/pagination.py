"""The pagination sections of a book, and the numbering of a range of pages that makes one.

A section numbers the pages from the page it starts at, and the labels of the pages are computed from the sections when
they are read, so a use case that changes a section writes no label, and the browser is told that the
pages changed once the transaction committed.

``number`` keeps the older way of numbering a range of pages, which is now a way of making sections. It makes a section
of the main flow at the first page of the range, a series that does not count for the kinds to skip, and a section that
does not count at the page after the range, unless a section starts there, so the pages after the range are not
numbered by it. The sections that started inside the range are replaced, and the pages the range numbers give up their
manual labels, which the older numbering overwrote too.
"""

from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING
from uuid import uuid4

from attrs import asdict, evolve, frozen

from bookreviver.domain.entities import PaginationSection
from bookreviver.domain.enums import LabelStyle, NumberDisplay
from bookreviver.domain.errors import ConflictError, NotFoundError, ReversedRangeError
from bookreviver.domain.ids import PaginationSectionId
from bookreviver.domain.pagination import Pagination
from bookreviver.domain.values import NumberedPage, Slice
from bookreviver.services.page_labels import PageLabels

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.entities import BookRecord, Page
    from bookreviver.domain.ids import PageId
    from bookreviver.domain.values import PageNumbering, PaginationSectionDraft, SliceRequest
    from bookreviver.ports.persistence import BookRepositories
    from bookreviver.services.book import Book

NUMBERED_PAGES: str = 'Numbered pages'
SKIPPED_KINDS: str = 'Pages that are not numbered'
AFTER_NUMBERED_PAGES: str = 'After the numbered pages'
SECTION_CLASH: str = 'Another section that numbers the same pages starts at this page already.'


@frozen(kw_only=True)
class NumberingPlan:
    """What numbering a range of pages does to the sections of a book, worked out and not yet written.

    :ivar sections: Every section of the book once the numbering is applied.
    :ivar removed: Identifiers of the sections the numbering replaces.
    :ivar added: The sections the numbering makes.
    :ivar pages: Every page of the book in book order.
    :ivar counted: Identifiers of the pages of the range that take a number, whose hand-written labels are dropped.
    """

    sections: Sequence[PaginationSection]
    removed: Sequence[PaginationSectionId]
    added: Sequence[PaginationSection]
    pages: Sequence[Page]
    counted: frozenset[PageId]

    def labels(self) -> dict[PageId, str]:
        """Compute the labels the numbering gives the pages of the book.

        :returns: The label of every page by identifier, with the pages of the range free of their own labels.
        :rtype: dict[PageId, str]
        :raises ConflictError: If a number does not fit its style, such as 4000 in Roman numerals.
        """
        pages = [evolve(page, manual_label=None) if page.id in self.counted else page for page in self.pages]
        try:
            return Pagination(self.sections, pages).labels()
        except ValueError as error:
            raise ConflictError(str(error)) from error


class PaginationService:
    """Lists, makes, changes and removes the pagination sections of the acting account's books."""

    def __init__(self) -> None:
        """Work over the ports of one request."""
        self._labels = PageLabels()

    async def sections(self, book: Book, request: SliceRequest) -> Slice[PaginationSection]:
        """List the sections of a book in book order, the order of the pages they start at.

        :param book: The open book.
        :type book: Book
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :returns: The sections of the window, those of the main flow before the series that start at the same page,
                  and the number of all the sections of the book.
        :rtype: Slice[PaginationSection]
        """

        def work(repos: BookRepositories) -> Slice[PaginationSection]:
            """Read the sections and order them by the pages they start at.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: The sections of the window, and the number of all the sections of the book.
            :rtype: Slice[PaginationSection]
            """
            sections = repos.pagination_sections.list_all()
            starts = repos.pages.list_by_ids({section.first_page_id for section in sections})
            position = {page.id: index for index, page in enumerate(starts)}
            ordered = sorted(sections, key=lambda section: (position[section.first_page_id], section.is_series))
            return Slice(items=ordered[request.offset : request.offset + request.limit], total=len(ordered))

        return await book.read(work)

    async def add(self, book: Book, draft: PaginationSectionDraft) -> PaginationSection:
        """Make a section, and renumber the pages it takes.

        :param book: The open book.
        :type book: Book
        :param draft: The page the section starts at, and its name, style, first number, prefix, display and kinds.
        :type draft: PaginationSectionDraft
        :returns: The section as stored.
        :rtype: PaginationSection
        :raises NotFoundError: If the book has no such page.
        :raises ConflictError: If another section that takes the same pages starts at the page, or a number does not
                               fit the style.
        """
        moment = datetime.now(UTC)
        section = PaginationSection(
            id=PaginationSectionId(uuid4()),
            **asdict(draft, recurse=False),
            created_at=moment,
            updated_at=moment,
        )

        def work(repos: BookRepositories, _book_record: BookRecord) -> PaginationSection:
            """Store the section and check the numbers of the pages it renumbers.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it.
            :type _book_record: BookRecord
            :returns: The section as stored.
            :rtype: PaginationSection
            :raises NotFoundError: If the book has no such page.
            :raises ConflictError: If another section that takes the same pages starts at the page, or a number does
                                   not fit the style.
            """
            repos.pages.get(section.first_page_id)
            self._require_no_clash(repos, section)
            stored = repos.pagination_sections.add(section)
            self._labels.settle(repos)
            return stored

        return await book.change(work)

    async def update(
        self, book: Book, section_id: PaginationSectionId, draft: PaginationSectionDraft
    ) -> PaginationSection:
        """Replace the page, the name and the rule of a section, and renumber the pages.

        :param book: The open book.
        :type book: Book
        :param section_id: Identifier of the section.
        :type section_id: PaginationSectionId
        :param draft: The new page, name, style, first number, prefix, display and kinds.
        :type draft: PaginationSectionDraft
        :returns: The section as stored.
        :rtype: PaginationSection
        :raises NotFoundError: If the book has no such section or page.
        :raises ConflictError: If another section that takes the same pages starts at the page, or a number does not
                               fit the style.
        """

        def work(repos: BookRepositories, _book_record: BookRecord) -> PaginationSection:
            """Replace the section and check the numbers of the pages it renumbers.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it.
            :type _book_record: BookRecord
            :returns: The section as stored.
            :rtype: PaginationSection
            :raises NotFoundError: If the book has no such section or page.
            :raises ConflictError: If another section that takes the same pages starts at the page, or a number does
                                   not fit the style.
            """
            section = evolve(
                repos.pagination_sections.get(section_id),
                **asdict(draft, recurse=False),
                updated_at=datetime.now(UTC),
            )
            repos.pages.get(section.first_page_id)
            self._require_no_clash(repos, section)
            stored = repos.pagination_sections.update(section)
            self._labels.settle(repos)
            return stored

        return await book.change(work)

    async def remove(self, book: Book, section_id: PaginationSectionId) -> None:
        """Remove a section, and renumber the pages it numbered, which fall to the section before it.

        :param book: The open book.
        :type book: Book
        :param section_id: Identifier of the section.
        :type section_id: PaginationSectionId
        :raises NotFoundError: If the book has no such section.
        :raises ConflictError: If the section before cannot write the numbers of the pages that fall to it.
        """

        def work(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Delete the section and check the numbers of the pages it renumbered.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it.
            :type _book_record: BookRecord
            :raises NotFoundError: If the book has no such section.
            :raises ConflictError: If the section before cannot write the numbers of the pages that fall to it.
            """
            section = repos.pagination_sections.get(section_id)
            repos.pagination_sections.delete(section.id)
            self._labels.settle(repos)

        await book.change(work)

    async def number(self, book: Book, numbering: PageNumbering) -> None:
        """Make the sections that number a range of pages, and drop the manual labels of the pages they number.

        :param book: The open book.
        :type book: Book
        :param numbering: The range, the style and the first number.
        :type numbering: PageNumbering
        :raises NotFoundError: If the book lacks the first or the last page.
        :raises ReversedRangeError: If the range runs backwards.
        :raises ConflictError: If a number does not fit the style, such as 4000 in Roman numerals.
        """

        def work(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Replace the sections the numbering replaces, and add the ones it makes.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it.
            :type _book_record: BookRecord
            :raises NotFoundError: If the book lacks the first or the last page.
            :raises ReversedRangeError: If the range runs backwards.
            :raises ConflictError: If a number does not fit the style, such as 4000 in Roman numerals.
            """
            plan = self._plan(repos, numbering)
            # A number that does not fit is found before anything is written
            plan.labels()
            for section_id in plan.removed:
                repos.pagination_sections.delete(section_id)
            repos.pagination_sections.add_many(plan.added)
            self._labels.settle(repos, unpin=plan.counted)

        await book.change(work)

    async def preview_numbers(self, book: Book, numbering: PageNumbering) -> list[NumberedPage]:
        """Give the labels a numbering would write, without writing any.

        The labels come from the same plan ``number`` writes, so a client shows exactly what saving would store.

        :param book: The open book.
        :type book: Book
        :param numbering: The range, the style and the first number.
        :type numbering: PageNumbering
        :returns: One entry for each page of the range that takes a number, in book order, with the label it would get.
        :rtype: list[NumberedPage]
        :raises NotFoundError: If the book lacks the first or the last page.
        :raises ReversedRangeError: If the range runs backwards.
        :raises ConflictError: If a number does not fit the style.
        """

        def work(repos: BookRepositories) -> NumberingPlan:
            """Work out the plan of the numbering on the book.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: The plan, with nothing written.
            :rtype: NumberingPlan
            :raises NotFoundError: If the book lacks the first or the last page.
            :raises ReversedRangeError: If the range runs backwards.
            """
            return self._plan(repos, numbering)

        plan = await book.read(work)
        labels = plan.labels()
        return [NumberedPage(page_id=page.id, label=labels[page.id]) for page in plan.pages if page.id in plan.counted]

    def _plan(self, repos: BookRepositories, numbering: PageNumbering) -> NumberingPlan:
        """Work out the sections a numbering makes and replaces, and the pages it numbers.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param numbering: The range, the style and the first number.
        :type numbering: PageNumbering
        :returns: The plan, with nothing written.
        :rtype: NumberingPlan
        :raises NotFoundError: If the book lacks the first or the last page.
        :raises ReversedRangeError: If the range runs backwards.
        """
        pages = repos.pages.list_all()
        position = {page.id: index for index, page in enumerate(pages)}
        if numbering.first_page_id not in position:
            raise NotFoundError(numbering.first_page_id)
        if numbering.last_page_id not in position:
            raise NotFoundError(numbering.last_page_id)
        first, last = position[numbering.first_page_id], position[numbering.last_page_id]
        if first > last:
            raise ReversedRangeError
        counted = frozenset(
            page.id for page in pages[first : last + 1] if page.included and page.kind not in numbering.skip_kinds
        )

        moment = datetime.now(UTC)
        make = partial(PaginationSection, created_at=moment, updated_at=moment)
        added = [
            make(
                id=PaginationSectionId(uuid4()),
                first_page_id=numbering.first_page_id,
                name=NUMBERED_PAGES,
                style=numbering.style,
                start=numbering.start,
                display=NumberDisplay.COUNTED if numbering.bracketed else NumberDisplay.PRINTED,
            )
        ]
        if numbering.skip_kinds:
            added.append(
                make(
                    id=PaginationSectionId(uuid4()),
                    first_page_id=numbering.first_page_id,
                    name=SKIPPED_KINDS,
                    style=LabelStyle.ARABIC,
                    display=NumberDisplay.NOT_COUNTED,
                    kinds=numbering.skip_kinds,
                )
            )

        # A section that starts inside the range is replaced, and a series only when it takes a kind that is skipped
        existing = repos.pagination_sections.list_all()
        removed = [
            section
            for section in existing
            if first <= position.get(section.first_page_id, -1) <= last
            and (not section.is_series or section.kinds & numbering.skip_kinds)
        ]
        kept = [section for section in existing if section not in removed]
        if last + 1 < len(pages) and not any(
            section.first_page_id == pages[last + 1].id and not section.is_series for section in kept
        ):
            added.append(
                make(
                    id=PaginationSectionId(uuid4()),
                    first_page_id=pages[last + 1].id,
                    name=AFTER_NUMBERED_PAGES,
                    style=LabelStyle.NONE,
                    display=NumberDisplay.NOT_COUNTED,
                )
            )
        return NumberingPlan(
            sections=[*kept, *added],
            removed=[section.id for section in removed],
            added=added,
            pages=pages,
            counted=counted,
        )

    @staticmethod
    def _require_no_clash(repos: BookRepositories, section: PaginationSection) -> None:
        """Require that no other section of the book takes the same pages from the same page.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param section: The section about to be stored.
        :type section: PaginationSection
        :raises ConflictError: If another section starts at the page and is of the same flow, or shares a kind.
        """
        others = repos.pagination_sections.list_all()
        if any(section.clashes_with(other) for other in others):
            raise ConflictError(SECTION_CLASH)
