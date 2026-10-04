"""Contract of the marks and comments of the results: the columns of a version and the log of their changes.

Every test runs against each adapter registered in the conftest, the in-memory one and the SQL one.
"""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve

from bookreviver.domain.enums import ResultMark
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.ids import PageVersionId
from tests.helpers.builders import (
    EPOCH,
    make_page,
    make_page_version,
    make_project,
    make_result_mark_change,
)

if TYPE_CHECKING:
    from bookreviver.domain.ids import PageId
    from tests.contracts.conftest import OwnerFactory, UnitOfWorkFactory

pytestmark = pytest.mark.anyio

COMMENT: str = 'Too tight on the left.\nTry the other method.'


async def _store_version(uow_factory: UnitOfWorkFactory, new_owner: OwnerFactory) -> tuple[PageId, PageVersionId]:
    """Store a project with one page and one version of it, and commit.

    :param uow_factory: Function opening a new unit of work of the backend under test.
    :type uow_factory: UnitOfWorkFactory
    :param new_owner: Function creating an account the backend accepts as an owner.
    :type new_owner: OwnerFactory
    :returns: The identifiers of the page and its version.
    :rtype: tuple[PageId, PageVersionId]
    """
    uow = await uow_factory()
    project = make_project(owner_id=await new_owner())
    page = make_page(project_id=project.id)
    version = make_page_version(page_id=page.id)
    await uow.projects.add(project)
    await uow.pages.add(page)
    await uow.page_versions.add(version)
    await uow.commit()
    return page.id, version.id


class TestResultMarkColumns:
    """Tests for the mark and the comment a version stores."""

    async def test_a_new_version_has_no_mark_and_no_comment(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a version stored without a judgement reads back without one.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, version_id = await _store_version(fx_uow_factory, fx_new_owner)
        version = await (await fx_uow_factory()).page_versions.get(version_id)
        assert (version.mark, version.comment) == (None, '')

    async def test_mark_and_comment_survive_an_update_and_can_be_taken_off(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a mark and a multi-line comment read back after an update, and an update without them clears them.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, version_id = await _store_version(fx_uow_factory, fx_new_owner)
        uow = await fx_uow_factory()
        version = await uow.page_versions.get(version_id)
        await uow.page_versions.update(evolve(version, mark=ResultMark.BAD, comment=COMMENT))
        await uow.commit()
        uow = await fx_uow_factory()
        marked = await uow.page_versions.get(version_id)
        await uow.page_versions.update(evolve(marked, mark=None, comment=''))
        await uow.commit()
        cleared = await (await fx_uow_factory()).page_versions.get(version_id)
        assert ((marked.mark, marked.comment), (cleared.mark, cleared.comment)) == (
            (ResultMark.BAD, COMMENT),
            (None, ''),
        )


class TestResultMarkChangeRepository:
    """Tests for the log of the marks and comments of the results."""

    async def test_change_survives_the_store(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a change reads back with its marks, its comments, its time and its sequence.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, version_id = await _store_version(fx_uow_factory, fx_new_owner)
        change = make_result_mark_change(
            version_id=version_id,
            mark_before=ResultMark.GOOD,
            mark_after=None,
            comment_after=COMMENT,
        )
        uow = await fx_uow_factory()
        stored = await uow.result_mark_changes.add(change)
        await uow.commit()
        assert (await (await fx_uow_factory()).result_mark_changes.get(change.id), stored) == (
            stored,
            evolve(change, sequence=1),
        )

    async def test_changes_made_at_the_same_instant_list_in_the_order_they_were_written(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the sequence, not the time or the identifier, orders the log, across transactions.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, version_id = await _store_version(fx_uow_factory, fx_new_owner)
        written = [make_result_mark_change(version_id=version_id, created_at=EPOCH) for _ in range(5)]
        uow = await fx_uow_factory()
        for change in written[:2]:
            await uow.result_mark_changes.add(change)
        await uow.commit()
        uow = await fx_uow_factory()
        for change in written[2:]:
            await uow.result_mark_changes.add(change)
        await uow.commit()
        listed = await (await fx_uow_factory()).result_mark_changes.list_for_version(version_id)
        assert ([change.id for change in listed], [change.sequence for change in listed]) == (
            [change.id for change in written],
            [1, 2, 3, 4, 5],
        )

    async def test_each_version_numbers_its_own_log(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the log of a second version starts at one and lists only its own changes.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        page_id, first_id = await _store_version(fx_uow_factory, fx_new_owner)
        uow = await fx_uow_factory()
        second = make_page_version(page_id=page_id, minutes=1)
        await uow.page_versions.add(second)
        first_change = make_result_mark_change(version_id=first_id)
        second_change = make_result_mark_change(version_id=second.id)
        await uow.result_mark_changes.add(first_change)
        await uow.result_mark_changes.add(second_change)
        listed = await uow.result_mark_changes.list_for_version(second.id)
        assert [(change.id, change.sequence) for change in listed] == [(second_change.id, 1)]

    async def test_change_of_a_missing_version_is_not_found(self, fx_uow_factory: UnitOfWorkFactory) -> None:
        """Reject a change whose version is not stored.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        """
        uow = await fx_uow_factory()
        with pytest.raises(NotFoundError):
            await uow.result_mark_changes.add(make_result_mark_change(version_id=PageVersionId('0' * 16)))

    async def test_change_is_stored_once(self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory) -> None:
        """Verify a change with an identifier that is stored already is a conflict.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, version_id = await _store_version(fx_uow_factory, fx_new_owner)
        change = make_result_mark_change(version_id=version_id)
        uow = await fx_uow_factory()
        await uow.result_mark_changes.add(change)
        await uow.commit()
        with pytest.raises(ConflictError):
            await (await fx_uow_factory()).result_mark_changes.add(change)

    async def test_deleting_the_version_deletes_its_log(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the log goes with its version, as the foreign key cascades.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        _, version_id = await _store_version(fx_uow_factory, fx_new_owner)
        uow = await fx_uow_factory()
        await uow.result_mark_changes.add(make_result_mark_change(version_id=version_id))
        await uow.commit()
        uow = await fx_uow_factory()
        await uow.page_versions.delete_many([version_id])
        await uow.commit()
        assert await (await fx_uow_factory()).result_mark_changes.list_for_version(version_id) == []

    async def test_deleting_the_page_deletes_the_log_of_its_versions(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the log of a version goes when its page is deleted.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        page_id, version_id = await _store_version(fx_uow_factory, fx_new_owner)
        uow = await fx_uow_factory()
        await uow.result_mark_changes.add(make_result_mark_change(version_id=version_id))
        await uow.commit()
        uow = await fx_uow_factory()
        await uow.pages.delete(page_id)
        await uow.commit()
        assert await (await fx_uow_factory()).result_mark_changes.list_for_version(version_id) == []
