"""Tests for the in-memory unit of work where it cannot share the contract suite with a database.

A database keeps a row changed by a guarded write locked until the transaction ends, so a second writer waits. The
in-memory adapter has no locks and refuses the first transaction's commit instead, which only this adapter can show
without blocking the test.
"""

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.persistence.memory import InMemoryDatabase, InMemoryUnitOfWork
from bookreviver.domain.enums import JobState
from bookreviver.domain.errors import ConflictError
from tests.helpers.builders import make_job, make_project, new_account_id

pytestmark = pytest.mark.anyio


class TestCommit:
    """Tests for InMemoryUnitOfWork.commit()."""

    async def test_guarded_write_changed_meanwhile_refuses_commit(self) -> None:
        """Verify a commit is refused, and nothing of it kept, when another unit committed its guarded job meanwhile.

        The canceller passes its guard while the job runs, then the worker finishes the job and commits first. The
        cancellation must not replace the finished job.
        """
        database = InMemoryDatabase()
        project = make_project(owner_id=new_account_id())
        job = make_job(project_id=project.id, state=JobState.RUNNING)
        setup = InMemoryUnitOfWork(database)
        await setup.projects.add(project)
        await setup.jobs.add(job)
        await setup.commit()
        canceller, worker = InMemoryUnitOfWork(database), InMemoryUnitOfWork(database)
        await canceller.jobs.update_if_state(evolve(job, state=JobState.CANCELLED), expected=JobState.active())
        await worker.jobs.update_if_state(evolve(job, state=JobState.SUCCEEDED), expected=JobState.active())
        await worker.commit()
        with pytest.raises(ConflictError, match=str(job.id)):
            await canceller.commit()
        expect((await InMemoryUnitOfWork(database).jobs.get(job.id)).state is JobState.SUCCEEDED)
        # The refused transaction is discarded, so the unit starts again from the committed state
        expect((await canceller.jobs.get(job.id)).state is JobState.SUCCEEDED)
        assert_expectations()
