"""Tests for the upload route that starts an import."""

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import JobKind, JobState, UploadProblem
from tests.helpers.builders import make_project, new_account_id

if TYPE_CHECKING:
    import httpx

    from bookreviver.domain.entities import Actor, Project
    from tests.helpers.fakes_imports import ImportFakes

pytestmark = pytest.mark.anyio

SOURCE_PATH: str = '/api/v1/projects/{project_id}/source'
PDF_NAME: str = 'book.pdf'
PDF_BYTES: bytes = b'%PDF-1.7 fake'
PDF_MEDIA_TYPE: str = 'application/pdf'
PNG_MEDIA_TYPE: str = 'image/png'


@pytest.fixture
async def fx_project(fx_import_fakes: ImportFakes, fx_actor: Actor) -> Project:
    """Store a project owned by the signed-in account."""
    project = make_project(owner_id=fx_actor.account_id)
    await fx_import_fakes.store(project)
    return project


class TestUploadSource:
    """Tests for POST /projects/{project_id}/source."""

    async def test_upload_is_accepted_with_queued_job(self, fx_client: httpx.AsyncClient, fx_project: Project) -> None:
        """Verify an accepted upload answers 202 with the job that will import it."""
        response = await fx_client.post(
            SOURCE_PATH.format(project_id=fx_project.id), files=[('files', (PDF_NAME, PDF_BYTES, PDF_MEDIA_TYPE))]
        )
        body = response.json()
        expect(response.status_code == HTTPStatus.ACCEPTED)
        expect(
            (body['project_id'], body['kind'], body['state'])
            == (str(fx_project.id), JobKind.IMPORT_SOURCE, JobState.QUEUED)
        )
        expect(body['progress'] == {'done': 0, 'total': 0, 'fraction': 0.0})
        assert_expectations()

    async def test_broken_upload_rule_is_bad_request(self, fx_client: httpx.AsyncClient, fx_project: Project) -> None:
        """Verify a file set breaking an upload rule answers with a problem naming the rule."""
        files = [('files', (PDF_NAME, PDF_BYTES, PDF_MEDIA_TYPE)), ('files', ('page.png', b'png', PNG_MEDIA_TYPE))]
        response = await fx_client.post(SOURCE_PATH.format(project_id=fx_project.id), files=files)
        expect(response.status_code == HTTPStatus.BAD_REQUEST)
        expect(response.json()['detail'] == UploadProblem.MIXED_TYPES.label)
        assert_expectations()

    async def test_project_of_another_account_is_not_found(
        self, fx_client: httpx.AsyncClient, fx_import_fakes: ImportFakes
    ) -> None:
        """Verify uploading into a project of another account answers 404."""
        project = make_project(owner_id=new_account_id())
        await fx_import_fakes.store(project)
        response = await fx_client.post(
            SOURCE_PATH.format(project_id=project.id), files=[('files', (PDF_NAME, PDF_BYTES, PDF_MEDIA_TYPE))]
        )
        assert response.status_code == HTTPStatus.NOT_FOUND
