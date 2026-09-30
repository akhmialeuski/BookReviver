"""Contract of the persistence ports, run against every adapter registered in the conftest."""

from operator import attrgetter
from typing import TYPE_CHECKING

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import JobState
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.values import BookDetails, SliceRequest
from tests.helpers.builders import make_job, make_page, make_project, new_account_id

if TYPE_CHECKING:
    from tests.contracts.conftest import UnitOfWorkFactory

pytestmark = pytest.mark.anyio

PAGE_COUNT: int = 3
EVERY_STATE: frozenset[JobState] = frozenset(JobState)
OPERATION_ARG: str = 'operation'
# The one single-entity operation that takes the entity rather than its identifier
UPDATE_OPERATION: str = 'update'


class TestProjectRepository:
    """Contract of ProjectRepository."""

    async def test_added_project_reads_back_after_commit(self, fx_uow_factory: UnitOfWorkFactory) -> None:
        """Verify a committed project is visible to a later unit of work, unchanged.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        """
        project = make_project(owner_id=new_account_id())
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.commit()
        assert await (await fx_uow_factory()).projects.get(project.id) == project

    async def test_rolled_back_project_is_gone(self, fx_uow_factory: UnitOfWorkFactory) -> None:
        """Verify rollback discards an uncommitted project.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        """
        project = make_project(owner_id=new_account_id())
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.rollback()
        with pytest.raises(NotFoundError):
            await (await fx_uow_factory()).projects.get(project.id)

    async def test_update_replaces_details(self, fx_uow_factory: UnitOfWorkFactory) -> None:
        """Verify an update stores the new description.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        """
        project = make_project(owner_id=new_account_id())
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        renamed = evolve(project, details=BookDetails(title='Renamed', authors='A. Author'))
        await uow.projects.update(renamed)
        await uow.commit()
        assert (await (await fx_uow_factory()).projects.get(project.id)).details == renamed.details

    async def test_adding_a_stored_project_raises_conflict(self, fx_uow_factory: UnitOfWorkFactory) -> None:
        """Verify adding a project under an identifier already stored is a ConflictError, never a silent overwrite.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        """
        project = make_project(owner_id=new_account_id())
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.commit()
        duplicate = evolve(project, details=BookDetails(title='Other'))
        uow = await fx_uow_factory()
        with pytest.raises(ConflictError, match=str(project.id)):
            await uow.projects.add(duplicate)

    @pytest.mark.parametrize(OPERATION_ARG, ['get', UPDATE_OPERATION, 'delete'])
    async def test_missing_project_raises_not_found(self, fx_uow_factory: UnitOfWorkFactory, operation: str) -> None:
        """Verify every single-entity operation on an unknown project raises NotFoundError naming the project.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param operation: Name of the repository method called with the unknown project.
        :type operation: str
        """
        project = make_project(owner_id=new_account_id())
        repository = (await fx_uow_factory()).projects
        argument = project if operation == UPDATE_OPERATION else project.id
        with pytest.raises(NotFoundError, match=str(project.id)):
            await getattr(repository, operation)(argument)

    async def test_list_for_owner_orders_pages_and_counts(self, fx_uow_factory: UnitOfWorkFactory) -> None:
        """Verify the owner's projects come newest first, sliced, with page counts, and others are hidden.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        """
        owner_id = new_account_id()
        older, newer = make_project(owner_id=owner_id, minutes=1), make_project(owner_id=owner_id, minutes=2)
        uow = await fx_uow_factory()
        for project in (older, newer, make_project(owner_id=new_account_id())):
            await uow.projects.add(project)
        await uow.pages.replace_for_project(
            older.id, [make_page(project_id=older.id, index=i) for i in range(PAGE_COUNT)]
        )
        await uow.commit()
        repository = (await fx_uow_factory()).projects
        full = await repository.list_for_owner(owner_id, SliceRequest())
        second = await repository.list_for_owner(owner_id, SliceRequest(offset=1, limit=1))
        expect([item.project.id for item in full.items] == [newer.id, older.id])
        expect([item.page_count for item in full.items] == [0, PAGE_COUNT])
        expect(full.total == 2)
        expect([item.project.id for item in second.items] == [older.id])
        expect(second.total == 2)
        assert_expectations()

    async def test_list_for_owner_breaks_ties_by_identifier(self, fx_uow_factory: UnitOfWorkFactory) -> None:
        """Verify projects updated at the same moment list by identifier, so offset paging never skips or repeats one.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        """
        owner_id = new_account_id()
        tied = sorted((make_project(owner_id=owner_id, minutes=1) for _ in range(PAGE_COUNT)), key=attrgetter('id'))
        uow = await fx_uow_factory()
        # Insert against the expected order, so neither insertion nor storage order can pass for it
        for project in reversed(tied):
            await uow.projects.add(project)
        await uow.commit()
        repository = (await fx_uow_factory()).projects
        full = await repository.list_for_owner(owner_id, SliceRequest())
        paged = [
            item.project.id
            for offset in range(PAGE_COUNT)
            for item in (await repository.list_for_owner(owner_id, SliceRequest(offset=offset, limit=1))).items
        ]
        expect([item.project.id for item in full.items] == [project.id for project in tied])
        expect(paged == [project.id for project in tied])
        assert_expectations()

    async def test_delete_cascades_to_pages_and_jobs(self, fx_uow_factory: UnitOfWorkFactory) -> None:
        """Verify deleting a project removes its pages and jobs and leaves other projects alone.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        """
        owner_id = new_account_id()
        doomed, kept = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        uow = await fx_uow_factory()
        for project in (doomed, kept):
            await uow.projects.add(project)
            await uow.pages.replace_for_project(project.id, [make_page(project_id=project.id, index=0)])
            await uow.jobs.add(make_job(project_id=project.id))
        await uow.projects.delete(doomed.id)
        await uow.commit()
        after = await fx_uow_factory()
        doomed_pages = await after.pages.list_for_project(doomed.id, SliceRequest())
        kept_pages = await after.pages.list_for_project(kept.id, SliceRequest())
        expect(doomed_pages.total == 0)
        expect(await after.jobs.list_for_project(doomed.id, EVERY_STATE) == [])
        expect(kept_pages.total == 1)
        expect(len(await after.jobs.list_for_project(kept.id, EVERY_STATE)) == 1)
        assert_expectations()


class TestPageRepository:
    """Contract of PageRepository."""

    async def test_replace_lists_in_book_order_and_slices(self, fx_uow_factory: UnitOfWorkFactory) -> None:
        """Verify replacing pages drops the old set and lists the new one by index.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        """
        project = make_project(owner_id=new_account_id())
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.pages.replace_for_project(project.id, [make_page(project_id=project.id, index=9)])
        await uow.pages.replace_for_project(
            project.id, [make_page(project_id=project.id, index=i) for i in reversed(range(PAGE_COUNT))]
        )
        await uow.commit()
        pages = (await fx_uow_factory()).pages
        everything = await pages.list_for_project(project.id, SliceRequest())
        tail = await pages.list_for_project(project.id, SliceRequest(offset=1, limit=PAGE_COUNT))
        expect([page.index for page in everything.items] == list(range(PAGE_COUNT)))
        expect(everything.total == PAGE_COUNT)
        expect([page.index for page in tail.items] == list(range(1, PAGE_COUNT)))
        assert_expectations()

    async def test_slice_past_the_end_reports_the_full_total(self, fx_uow_factory: UnitOfWorkFactory) -> None:
        """Verify an empty slice past the last page still reports every page, which pagination controls rely on.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        """
        project = make_project(owner_id=new_account_id())
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.pages.replace_for_project(
            project.id, [make_page(project_id=project.id, index=i) for i in range(PAGE_COUNT)]
        )
        await uow.commit()
        beyond = await (await fx_uow_factory()).pages.list_for_project(
            project.id, SliceRequest(offset=PAGE_COUNT, limit=1)
        )
        expect(list(beyond.items) == [])
        expect(beyond.total == PAGE_COUNT)
        assert_expectations()

    async def test_replace_stores_pages_under_the_given_project(self, fx_uow_factory: UnitOfWorkFactory) -> None:
        """Verify a page naming another project is stored under the replaced one, so no caller writes across books.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        """
        owner_id = new_account_id()
        target, other = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        uow = await fx_uow_factory()
        for project in (target, other):
            await uow.projects.add(project)
        await uow.pages.replace_for_project(target.id, [make_page(project_id=other.id, index=0)])
        await uow.commit()
        pages = (await fx_uow_factory()).pages
        expect((await pages.list_for_project(target.id, SliceRequest())).total == 1)
        expect((await pages.list_for_project(other.id, SliceRequest())).total == 0)
        assert_expectations()

    async def test_update_and_get_round_trip(self, fx_uow_factory: UnitOfWorkFactory) -> None:
        """Verify an updated page, including its assets, reads back unchanged.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        """
        project = make_project(owner_id=new_account_id())
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        page = make_page(project_id=project.id, index=0)
        await uow.pages.replace_for_project(project.id, [page])
        ready = evolve(page, assets=evolve(page.assets, ready=True, version=page.assets.version + 1))
        await uow.pages.update(ready)
        await uow.commit()
        assert await (await fx_uow_factory()).pages.get(project.id, 0) == ready

    async def test_replacing_pages_of_a_missing_project_raises_not_found(
        self, fx_uow_factory: UnitOfWorkFactory
    ) -> None:
        """Verify pages cannot be stored for a project that does not exist, so no page outlives its book.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        """
        project_id = make_project(owner_id=new_account_id()).id
        uow = await fx_uow_factory()
        with pytest.raises(NotFoundError, match=str(project_id)):
            await uow.pages.replace_for_project(project_id, [make_page(project_id=project_id, index=0)])

    async def test_missing_page_raises_not_found(self, fx_uow_factory: UnitOfWorkFactory) -> None:
        """Verify reading a page that does not exist raises NotFoundError naming its project.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        """
        project = make_project(owner_id=new_account_id())
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        with pytest.raises(NotFoundError, match=str(project.id)):
            await uow.pages.get(project.id, 0)


class TestJobRepository:
    """Contract of JobRepository."""

    async def test_list_filters_by_state_newest_first(self, fx_uow_factory: UnitOfWorkFactory) -> None:
        """Verify only jobs in the asked states are listed, newest first.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        """
        project = make_project(owner_id=new_account_id())
        old = make_job(project_id=project.id, state=JobState.RUNNING, minutes=1)
        new = make_job(project_id=project.id, state=JobState.QUEUED, minutes=2)
        done = make_job(project_id=project.id, state=JobState.SUCCEEDED, minutes=3)
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        for job in (old, new, done):
            await uow.jobs.add(job)
        await uow.commit()
        active = await (await fx_uow_factory()).jobs.list_for_project(project.id, {JobState.QUEUED, JobState.RUNNING})
        assert [job.id for job in active] == [new.id, old.id]

    async def test_job_of_a_missing_project_raises_not_found(self, fx_uow_factory: UnitOfWorkFactory) -> None:
        """Verify a job cannot be stored for a project that does not exist, so no job outlives its book.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        """
        project_id = make_project(owner_id=new_account_id()).id
        uow = await fx_uow_factory()
        with pytest.raises(NotFoundError, match=str(project_id)):
            await uow.jobs.add(make_job(project_id=project_id))

    async def test_guarded_update_replaces_job_in_expected_state(self, fx_uow_factory: UnitOfWorkFactory) -> None:
        """Verify a guarded write replaces a job whose state is expected, and the change reads back after commit.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        """
        project = make_project(owner_id=new_account_id())
        job = make_job(project_id=project.id, state=JobState.RUNNING)
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.jobs.add(job)
        await uow.commit()
        succeeded = evolve(job, state=JobState.SUCCEEDED)
        expect(await uow.jobs.update_if_state(succeeded, expected=JobState.active()) == succeeded)
        await uow.commit()
        expect(await (await fx_uow_factory()).jobs.get(job.id) == succeeded)
        assert_expectations()

    async def test_guarded_update_judges_state_committed_meanwhile(self, fx_uow_factory: UnitOfWorkFactory) -> None:
        """Verify a guarded write judges the state another unit committed after this one read the job, and keeps it.

        This is a cancellation racing a worker: the canceller reads the job running, the worker commits it as
        succeeded, and the cancellation must then leave the job succeeded.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        """
        project = make_project(owner_id=new_account_id())
        job = make_job(project_id=project.id, state=JobState.RUNNING)
        setup = await fx_uow_factory()
        await setup.projects.add(project)
        await setup.jobs.add(job)
        await setup.commit()
        canceller = await fx_uow_factory()
        seen = await canceller.jobs.get(job.id)
        worker = await fx_uow_factory()
        await worker.jobs.update(evolve(job, state=JobState.SUCCEEDED))
        await worker.commit()
        cancelled = evolve(seen, state=JobState.CANCELLED)
        expect(await canceller.jobs.update_if_state(cancelled, expected=JobState.active()) is None)
        await canceller.commit()
        expect((await (await fx_uow_factory()).jobs.get(job.id)).state is JobState.SUCCEEDED)
        assert_expectations()

    async def test_guarded_update_of_missing_job_raises_not_found(self, fx_uow_factory: UnitOfWorkFactory) -> None:
        """Verify a guarded write of a job that is not stored reports it missing rather than in another state.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        """
        job = make_job(project_id=make_project(owner_id=new_account_id()).id)
        uow = await fx_uow_factory()
        with pytest.raises(NotFoundError, match=str(job.id)):
            await uow.jobs.update_if_state(job, expected=JobState.active())


class TestUnitOfWork:
    """Contract of UnitOfWork isolation between concurrent units."""

    async def test_concurrent_commits_keep_each_others_changes(self, fx_uow_factory: UnitOfWorkFactory) -> None:
        """Verify a commit publishes only its own changes and never reverts another unit's commit.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        """
        owner_id = new_account_id()
        first, second = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        # Both units start before either commits, as two overlapping requests do
        early = await fx_uow_factory()
        late = await fx_uow_factory()
        await late.projects.add(second)
        await late.commit()
        await early.projects.add(first)
        await early.commit()
        listed = await (await fx_uow_factory()).projects.list_for_owner(owner_id, SliceRequest())
        assert {item.project.id for item in listed.items} == {first.id, second.id}
