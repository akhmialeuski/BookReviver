"""Contract of the marks and comments of the results: the columns of a version and the log of their changes.

Every test runs against the SQL adapter.
"""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve

from bookreviver.domain.enums import ResultMark
from tests.helpers.builders import (
    make_page,
    make_page_version,
    make_record,
)
from tests.helpers.seeding import store_book

if TYPE_CHECKING:
    from bookreviver.domain.ids import BookId, PageId, PageVersionId
    from tests.contracts.conftest import OwnerFactory
    from tests.helpers.database import UnitOfWorkFactory

pytestmark = pytest.mark.anyio

COMMENT: str = 'Too tight on the left.\nTry the other method.'


async def _store_version(
    uow_factory: UnitOfWorkFactory, new_owner: OwnerFactory
) -> tuple[BookId, PageId, PageVersionId]:
    """Store a project with one page and one version of it, each in the change that owns it.

    :param uow_factory: Function opening a new unit of work over the database of the test.
    :type uow_factory: UnitOfWorkFactory
    :param new_owner: Function creating an account the database accepts as an owner.
    :type new_owner: OwnerFactory
    :returns: The identifiers of the project, its page and the version of the page.
    :rtype: tuple[BookId, PageId, PageVersionId]
    """
    book_record = make_record()
    page = make_page()
    version = make_page_version(page_id=page.id)
    await store_book(uow_factory(), book_record, page, owner=await new_owner(), versions=[version])
    return book_record.id, page.id, version.id


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
        book_id, _, version_id = await _store_version(fx_uow, fx_new_owner)
        version = await fx_uow().read_book(book_id, lambda repos: repos.page_versions.get(version_id))
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
        book_id, _, version_id = await _store_version(fx_uow, fx_new_owner)
        uow = fx_uow()
        version = await uow.read_book(book_id, lambda repos: repos.page_versions.get(version_id))
        await uow.change_book(
            book_id,
            lambda repos, _book_record: repos.page_versions.update(
                evolve(version, mark=ResultMark.BAD, comment=COMMENT)
            ),
        )
        marked = await fx_uow().read_book(book_id, lambda repos: repos.page_versions.get(version_id))
        await fx_uow().change_book(
            book_id,
            lambda repos, _book_record: repos.page_versions.update(evolve(marked, mark=None, comment='')),
        )
        cleared = await fx_uow().read_book(book_id, lambda repos: repos.page_versions.get(version_id))
        assert ((marked.mark, marked.comment), (cleared.mark, cleared.comment)) == (
            (ResultMark.BAD, COMMENT),
            (None, ''),
        )
