"""Contract of the marks and comments of the results: the columns of a version and the log of their changes.

Every test runs against the SQL adapter.
"""

from functools import partial
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
from tests.helpers.seeding import store_project

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.entities import Project, ResultMarkChange
    from bookreviver.domain.ids import PageId, ProjectId
    from bookreviver.ports.persistence import BookRepositories
    from tests.contracts.conftest import OwnerFactory
    from tests.helpers.database import UnitOfWorkFactory

pytestmark = pytest.mark.anyio

COMMENT: str = 'Too tight on the left.\nTry the other method.'


async def _store_version(
    uow_factory: UnitOfWorkFactory, new_owner: OwnerFactory
) -> tuple[ProjectId, PageId, PageVersionId]:
    """Store a project with one page and one version of it, each in the change that owns it.

    :param uow_factory: Function opening a new unit of work over the database of the test.
    :type uow_factory: UnitOfWorkFactory
    :param new_owner: Function creating an account the database accepts as an owner.
    :type new_owner: OwnerFactory
    :returns: The identifiers of the project, its page and the version of the page.
    :rtype: tuple[ProjectId, PageId, PageVersionId]
    """
    project = make_project()
    page = make_page()
    version = make_page_version(page_id=page.id)
    await store_project(uow_factory(), project, page, owner=await new_owner(), versions=[version])
    return project.id, page.id, version.id


class TestResultMarkColumns:
    """Tests for the mark and the comment a version stores."""

    async def test_a_new_version_has_no_mark_and_no_comment(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a version stored without a judgement reads back without one.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, _, version_id = await _store_version(fx_uow, fx_new_owner)
        version = await fx_uow().read_book(project_id, lambda repos: repos.page_versions.get(version_id))
        assert (version.mark, version.comment) == (None, '')

    async def test_mark_and_comment_survive_an_update_and_can_be_taken_off(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a mark and a multi-line comment read back after an update, and an update without them clears them.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, _, version_id = await _store_version(fx_uow, fx_new_owner)
        uow = fx_uow()
        version = await uow.read_book(project_id, lambda repos: repos.page_versions.get(version_id))
        await uow.change_book(
            project_id,
            lambda repos, _project: repos.page_versions.update(evolve(version, mark=ResultMark.BAD, comment=COMMENT)),
        )
        marked = await fx_uow().read_book(project_id, lambda repos: repos.page_versions.get(version_id))
        await fx_uow().change_book(
            project_id,
            lambda repos, _project: repos.page_versions.update(evolve(marked, mark=None, comment='')),
        )
        cleared = await fx_uow().read_book(project_id, lambda repos: repos.page_versions.get(version_id))
        assert ((marked.mark, marked.comment), (cleared.mark, cleared.comment)) == (
            (ResultMark.BAD, COMMENT),
            (None, ''),
        )


class TestResultMarkChangeRepository:
    """Tests for the log of the marks and comments of the results."""

    async def test_change_survives_the_store(self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory) -> None:
        """Verify a change reads back with its marks, its comments, its time and its sequence.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, _, version_id = await _store_version(fx_uow, fx_new_owner)
        change = make_result_mark_change(
            version_id=version_id,
            mark_before=ResultMark.GOOD,
            mark_after=None,
            comment_after=COMMENT,
        )
        stored = await fx_uow().change_book(project_id, lambda repos, _project: repos.result_mark_changes.add(change))
        read = await fx_uow().read_book(project_id, lambda repos: repos.result_mark_changes.get(change.id))
        assert (read, stored) == (stored, evolve(change, sequence=1))

    async def test_changes_made_at_the_same_instant_list_in_the_order_they_were_written(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the sequence, not the time or the identifier, orders the log, across transactions.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, _, version_id = await _store_version(fx_uow, fx_new_owner)
        written = [make_result_mark_change(version_id=version_id, created_at=EPOCH) for _ in range(5)]

        def add_all(changes: Sequence[ResultMarkChange], repos: BookRepositories, _project: Project) -> None:
            """Add the changes one by one, as separate writes of the log.

            :param changes: The changes to add, in the order to write them.
            :type changes: Sequence[ResultMarkChange]
            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            for change in changes:
                repos.result_mark_changes.add(change)

        await fx_uow().change_book(project_id, partial(add_all, written[:2]))
        await fx_uow().change_book(project_id, partial(add_all, written[2:]))
        listed = await fx_uow().read_book(
            project_id, lambda repos: repos.result_mark_changes.list_for_version(version_id)
        )
        assert ([change.id for change in listed], [change.sequence for change in listed]) == (
            [change.id for change in written],
            [1, 2, 3, 4, 5],
        )

    async def test_each_version_numbers_its_own_log(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the log of a second version starts at one and lists only its own changes.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, page_id, first_id = await _store_version(fx_uow, fx_new_owner)
        uow = fx_uow()
        second = make_page_version(page_id=page_id, minutes=1)
        first_change = make_result_mark_change(version_id=first_id)
        second_change = make_result_mark_change(version_id=second.id)

        def add_versions_and_changes(repos: BookRepositories, _project: Project) -> None:
            """Add the second version and one change of each version.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            repos.page_versions.add(second)
            repos.result_mark_changes.add(first_change)
            repos.result_mark_changes.add(second_change)

        await uow.change_book(project_id, add_versions_and_changes)
        listed = await uow.read_book(project_id, lambda repos: repos.result_mark_changes.list_for_version(second.id))
        assert [(change.id, change.sequence) for change in listed] == [(second_change.id, 1)]

    async def test_change_of_a_missing_version_is_not_found(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Reject a change whose version is not stored.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, _, _ = await _store_version(fx_uow, fx_new_owner)
        change = make_result_mark_change(version_id=PageVersionId('0' * 16))
        with pytest.raises(NotFoundError):
            await fx_uow().change_book(project_id, lambda repos, _project: repos.result_mark_changes.add(change))

    async def test_change_is_stored_once(self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory) -> None:
        """Verify a change with an identifier that is stored already is a conflict.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, _, version_id = await _store_version(fx_uow, fx_new_owner)
        change = make_result_mark_change(version_id=version_id)
        await fx_uow().change_book(project_id, lambda repos, _project: repos.result_mark_changes.add(change))
        with pytest.raises(ConflictError):
            await fx_uow().change_book(project_id, lambda repos, _project: repos.result_mark_changes.add(change))

    async def test_deleting_the_version_deletes_its_log(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the log goes with its version, as the foreign key cascades.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, _, version_id = await _store_version(fx_uow, fx_new_owner)
        change = make_result_mark_change(version_id=version_id)
        await fx_uow().change_book(project_id, lambda repos, _project: repos.result_mark_changes.add(change))
        await fx_uow().change_book(project_id, lambda repos, _project: repos.page_versions.delete_many([version_id]))
        listed = await fx_uow().read_book(
            project_id, lambda repos: repos.result_mark_changes.list_for_version(version_id)
        )
        assert listed == []

    async def test_deleting_the_page_deletes_the_log_of_its_versions(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the log of a version goes when its page is deleted.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project_id, page_id, version_id = await _store_version(fx_uow, fx_new_owner)
        change = make_result_mark_change(version_id=version_id)
        await fx_uow().change_book(project_id, lambda repos, _project: repos.result_mark_changes.add(change))
        await fx_uow().change_book(project_id, lambda repos, _project: repos.pages.delete(page_id))
        listed = await fx_uow().read_book(
            project_id, lambda repos: repos.result_mark_changes.list_for_version(version_id)
        )
        assert listed == []
