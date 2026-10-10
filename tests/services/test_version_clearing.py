"""Tests for the deletion of a batch of versions, which reads the versions again in the block that marks them."""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.domain.enums import VersionData, VersionState
from bookreviver.services.version_clearing import BEING_COLLECTED, VersionClearing
from tests.helpers.builders import make_page, make_page_version, make_project, new_account_id
from tests.helpers.seeding import commit_project

if TYPE_CHECKING:
    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.adapters.storage import LocalAssetStore

pytestmark = pytest.mark.anyio


class TestClear:
    """Tests for ``VersionClearing.clear``."""

    async def test_a_version_deleted_after_the_collection_chose_it_is_skipped_and_the_others_go(
        self, fx_database: InMemoryDatabase, fx_asset_store: LocalAssetStore
    ) -> None:
        """Verify a version a request deleted meanwhile neither fails the batch nor keeps the others from going.

        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        """
        project = make_project(owner_id=new_account_id())
        page = make_page(project_id=project.id)
        kept, deleted = (
            evolve(make_page_version(page_id=page.id, minutes=n), state=VersionState.READY) for n in (1, 2)
        )
        await commit_project(fx_database, project, page, versions=[kept, deleted])
        uow = InMemoryUnitOfWork(fx_database)
        async with uow.change_book(project.id):
            await uow.page_versions.delete_many([deleted.id])
        clearing = VersionClearing(uow=InMemoryUnitOfWork(fx_database), assets=fx_asset_store, project_id=project.id)

        await clearing.clear([kept, deleted])

        expect(fx_database.tables.page_versions == {})
        assert_expectations()

    async def test_the_mark_of_the_user_written_after_the_collection_chose_the_version_is_kept_on_the_marked_row(
        self, fx_database: InMemoryDatabase, fx_asset_store: LocalAssetStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify the version is marked from the row the block reads, so a comment written meanwhile is not lost.

        The directory cannot be removed in this test, so the version stays and its marked row can be read.

        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param monkeypatch: Fixture that restores the patched store after the test.
        :type monkeypatch: pytest.MonkeyPatch
        """
        project = make_project(owner_id=new_account_id())
        page = make_page(project_id=project.id)
        chosen = evolve(make_page_version(page_id=page.id), state=VersionState.READY)
        await commit_project(fx_database, project, page, versions=[chosen])
        uow = InMemoryUnitOfWork(fx_database)
        async with uow.change_book(project.id):
            await uow.page_versions.update(evolve(chosen, comment='keep'))

        monkeypatch.setattr(fx_asset_store, 'delete_prefix', AsyncMock(side_effect=OSError))
        clearing = VersionClearing(uow=InMemoryUnitOfWork(fx_database), assets=fx_asset_store, project_id=project.id)

        await clearing.clear([chosen])

        stored = fx_database.tables.page_versions[chosen.id]
        expect((stored.state, stored.comment) == (VersionState.FAILED, 'keep'))
        expect(stored.data[VersionData.ERROR] == BEING_COLLECTED)
        assert_expectations()
