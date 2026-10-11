"""Contract of the persistence port for the pagination sections of a book, run against every adapter.

The in-memory and the SQL adapter keep the same promises: the order of a listing, the fields that survive the store,
the foreign keys to the project and to the page a section starts at, and the removal of a section with either.
"""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve

from bookreviver.domain.enums import LabelStyle, NumberDisplay, PageKind
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import PageId
from tests.helpers.builders import make_page, make_project, make_section, new_account_id
from tests.helpers.seeding import store_project

if TYPE_CHECKING:
    from bookreviver.domain.entities import Page
    from bookreviver.domain.ids import ProjectId
    from bookreviver.ports.persistence import UnitOfWork
    from tests.contracts.conftest import OwnerFactory, UnitOfWorkFactory

pytestmark = pytest.mark.anyio

PREFACE_NAME: str = 'Preface'
PLATE_PREFIX: str = 'Plate '
FIRST_MINUTE: int = 1
SECOND_MINUTE: int = 2
THIRD_MINUTE: int = 3
THREE_PAGES: int = 3
SECOND_PAGE: int = 1


async def _store_book(
    uow_factory: UnitOfWorkFactory, new_owner: OwnerFactory
) -> tuple[UnitOfWork, ProjectId, list[Page]]:
    """Store a project of three pages in the blocks that own them.

    :param uow_factory: Function opening a new unit of work of the backend under test.
    :type uow_factory: UnitOfWorkFactory
    :param new_owner: Function creating an account the backend accepts as an owner.
    :type new_owner: OwnerFactory
    :returns: A new unit of work with no block open, the project and its pages in book order.
    :rtype: tuple[UnitOfWork, ProjectId, list[Page]]
    """
    project = make_project(owner_id=await new_owner())
    pages = [make_page(project_id=project.id, order_key=f'a{index}') for index in range(THREE_PAGES)]
    await store_project(await uow_factory(), project, *pages)
    return await uow_factory(), project.id, pages


class TestPaginationSectionRepository:
    """Tests for the pagination sections of the books."""

    async def test_sections_are_listed_in_the_order_they_were_made(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a listing holds the sections of its project by their creation time, whatever order they were stored in.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        uow, project_id, pages = await _store_book(fx_uow_factory, fx_new_owner)
        later = make_section(page=pages[SECOND_PAGE], minutes=THIRD_MINUTE)
        earlier = make_section(page=pages[0], minutes=FIRST_MINUTE)
        middle = make_section(page=pages[2], minutes=SECOND_MINUTE)
        async with uow.change_book(project_id):
            await uow.pagination_sections.add_many([later, earlier, middle])
        listed = await (await fx_uow_factory()).pagination_sections.list_for_project(project_id)
        assert listed == [earlier, middle, later]

    async def test_sections_of_another_project_are_left_out(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a listing never holds the sections of another project.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        uow, project_id, pages = await _store_book(fx_uow_factory, fx_new_owner)
        other = make_project(owner_id=await fx_new_owner())
        foreign_page = make_page(project_id=other.id)
        await store_project(uow, other, foreign_page)
        own = make_section(page=pages[0])
        async with uow.change_book(project_id):
            await uow.pagination_sections.add(own)
        async with uow.change_book(other.id):
            await uow.pagination_sections.add(make_section(page=foreign_page))
        listed = await (await fx_uow_factory()).pagination_sections.list_for_project(project_id)
        assert listed == [own]

    async def test_every_field_of_a_section_survives_the_store(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the style, the first number, the prefix, the display and the kinds of a series read back unchanged.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        uow, project_id, pages = await _store_book(fx_uow_factory, fx_new_owner)
        section = evolve(
            make_section(
                page=pages[SECOND_PAGE],
                style=LabelStyle.ROMAN_UPPER,
                display=NumberDisplay.COUNTED,
                kinds=frozenset({PageKind.PLATE, PageKind.FRONTISPIECE}),
            ),
            name=PREFACE_NAME,
            start=THREE_PAGES,
            prefix=PLATE_PREFIX,
        )
        async with uow.change_book(project_id):
            await uow.pagination_sections.add(section)
        assert await (await fx_uow_factory()).pagination_sections.get(section.id) == section

    async def test_a_section_is_replaced_whole(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify an update changes the page, the rule and the kinds of a section, and keeps its identifier.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        uow, project_id, pages = await _store_book(fx_uow_factory, fx_new_owner)
        section = make_section(page=pages[0])
        changed = evolve(
            section,
            first_page_id=pages[SECOND_PAGE].id,
            style=LabelStyle.ALPHA_LOWER,
            display=NumberDisplay.NOT_COUNTED,
            kinds=frozenset({PageKind.BLANK}),
        )
        async with uow.change_book(project_id):
            await uow.pagination_sections.add(section)
            await uow.pagination_sections.update(changed)
        assert await (await fx_uow_factory()).pagination_sections.list_for_project(project_id) == [changed]

    async def test_a_section_needs_its_first_page(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Reject a section whose first page is not stored.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        uow, project_id, pages = await _store_book(fx_uow_factory, fx_new_owner)
        section = make_section(page=pages[0])
        with pytest.raises(NotFoundError):
            async with uow.change_book(project_id):
                await uow.pagination_sections.add(evolve(section, first_page_id=PageId(new_account_id())))

    async def test_deleting_the_first_page_deletes_the_section(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a section goes with the page it starts at, and the sections of other pages stay.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        uow, project_id, pages = await _store_book(fx_uow_factory, fx_new_owner)
        doomed, kept = make_section(page=pages[0]), make_section(page=pages[SECOND_PAGE], minutes=FIRST_MINUTE)
        async with uow.change_book(project_id):
            await uow.pagination_sections.add_many([doomed, kept])
        uow = await fx_uow_factory()
        async with uow.change_book(project_id):
            await uow.pages.delete(pages[0].id)
        assert await (await fx_uow_factory()).pagination_sections.list_for_project(project_id) == [kept]

    async def test_deleting_the_project_deletes_its_sections(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the sections go with their project.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        uow, project_id, pages = await _store_book(fx_uow_factory, fx_new_owner)
        section = make_section(page=pages[0])
        async with uow.change_book(project_id):
            await uow.pagination_sections.add(section)
        uow = await fx_uow_factory()
        async with uow.change():
            await uow.projects.delete(project_id)
        with pytest.raises(NotFoundError):
            await (await fx_uow_factory()).pagination_sections.get(section.id)

    async def test_a_section_is_removed_by_its_identifier(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a deleted section is gone and deleting it again reports it missing.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        uow, project_id, pages = await _store_book(fx_uow_factory, fx_new_owner)
        section = make_section(page=pages[0])
        async with uow.change_book(project_id):
            await uow.pagination_sections.add(section)
        uow = await fx_uow_factory()
        async with uow.change_book(project_id):
            await uow.pagination_sections.delete(section.id)
        uow = await fx_uow_factory()
        assert await uow.pagination_sections.list_for_project(project_id) == []
        with pytest.raises(NotFoundError):
            async with uow.change_book(project_id):
                await uow.pagination_sections.delete(section.id)
