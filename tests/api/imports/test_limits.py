"""Tests for the limits of an upload: its number of files and its size, answered as RFC 9457 problems."""

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import JobState, RejectionReason, UploadProblem

if TYPE_CHECKING:
    import httpx
    from taskiq import InMemoryBroker

    from bookreviver.app.settings import Settings
    from bookreviver.domain.entities import Project

pytestmark = pytest.mark.anyio

SOURCES_PATH: str = '/api/v1/projects/{project_id}/sources'
JOB_PATH: str = '/api/v1/jobs/{job_id}'
FILES_FIELD: str = 'files'
PROBLEM_MEDIA_TYPE: str = 'application/problem+json'
CONTENT_TYPE_HEADER: str = 'content-type'
# The number of files Starlette's multipart parser accepts unless told otherwise
PARSER_DEFAULT_FILES: int = 1000
MAX_FILES: int = 1005
MAX_BYTES: int = 4096


def _text_files(count: int, *, size: int = 4) -> list[tuple[str, tuple[str, bytes, str]]]:
    """Build the parts of a request that carries ``count`` small text files, which no source can be made of.

    :param count: Number of files.
    :type count: int
    :param size: Size of each file in bytes.
    :type size: int
    :returns: Parts named ``files``, each with its own file name.
    :rtype: list[tuple[str, tuple[str, bytes, str]]]
    """
    return [(FILES_FIELD, (f'note-{number}.txt', b'x' * size, 'text/plain')) for number in range(count)]


@pytest.fixture
def fx_settings(fx_settings: Settings) -> Settings:
    """Override the suite's settings with the limits of these tests.

    :param fx_settings: Settings of the whole suite, with in-memory persistence and a fresh data directory.
    :type fx_settings: Settings
    :returns: The same settings with a file limit above the parser's default and a small size limit.
    :rtype: Settings
    """
    return fx_settings.model_copy(update={'max_upload_files': MAX_FILES, 'max_upload_bytes': MAX_BYTES})


class TestUploadLimits:
    """Tests for the limits of POST /projects/{project_id}/sources."""

    async def test_upload_of_more_files_than_the_parser_accepts_by_default_is_accepted(
        self, fx_client: httpx.AsyncClient, fx_broker: InMemoryBroker, fx_project: Project
    ) -> None:
        """Verify an upload may hold more than 1000 files, as the upload rule allows, and each one is answered for.

        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_broker: In-process broker running the import job.
        :type fx_broker: InMemoryBroker
        :param fx_project: Project of the signed-in account.
        :type fx_project: Project
        """
        response = await fx_client.post(
            SOURCES_PATH.format(project_id=fx_project.id), files=_text_files(PARSER_DEFAULT_FILES + 1, size=1)
        )
        assert response.status_code == HTTPStatus.ACCEPTED, response.text
        await fx_broker.wait_all()

        finished = (await fx_client.get(JOB_PATH.format(job_id=response.json()['id']))).json()
        expect(finished['state'] == JobState.FAILED)
        expect(len(finished['result']['rejected']) == PARSER_DEFAULT_FILES + 1)
        expect({file['reason'] for file in finished['result']['rejected']} == {RejectionReason.UNSUPPORTED_TYPE})
        assert_expectations()

    async def test_upload_of_one_file_more_than_the_rule_allows_is_a_413_problem(
        self, fx_client: httpx.AsyncClient, fx_project: Project
    ) -> None:
        """Verify an upload of one file too many is answered with a content too large problem naming the rule.

        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_project: Project of the signed-in account.
        :type fx_project: Project
        """
        response = await fx_client.post(
            SOURCES_PATH.format(project_id=fx_project.id), files=_text_files(MAX_FILES + 1, size=1)
        )

        expect(response.status_code == HTTPStatus.CONTENT_TOO_LARGE)
        expect(response.headers[CONTENT_TYPE_HEADER].startswith(PROBLEM_MEDIA_TYPE))
        expect(response.json()['detail'] == UploadProblem.TOO_MANY_FILES.label)
        assert_expectations()

    async def test_upload_of_two_files_more_than_the_rule_allows_is_refused_by_the_parser(
        self, fx_client: httpx.AsyncClient, fx_project: Project
    ) -> None:
        """Verify an upload far past the rule is stopped by the multipart parser before any file is received.

        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_project: Project of the signed-in account.
        :type fx_project: Project
        """
        response = await fx_client.post(
            SOURCES_PATH.format(project_id=fx_project.id), files=_text_files(MAX_FILES + 2, size=1)
        )

        expect(response.status_code == HTTPStatus.BAD_REQUEST)
        expect(response.headers[CONTENT_TYPE_HEADER].startswith(PROBLEM_MEDIA_TYPE))
        assert_expectations()

    async def test_upload_larger_than_the_rule_allows_is_a_413_problem(
        self, fx_client: httpx.AsyncClient, fx_project: Project
    ) -> None:
        """Verify an upload whose files together pass the size limit is answered with a content too large problem.

        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_project: Project of the signed-in account.
        :type fx_project: Project
        """
        response = await fx_client.post(
            SOURCES_PATH.format(project_id=fx_project.id), files=_text_files(2, size=MAX_BYTES)
        )

        expect(response.status_code == HTTPStatus.CONTENT_TOO_LARGE)
        expect(response.json()['detail'] == UploadProblem.TOO_LARGE.label)
        assert_expectations()
