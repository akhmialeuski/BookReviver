"""Contract of the SourceStore port, run against every storage adapter registered in the conftest."""

from itertools import starmap
from typing import TYPE_CHECKING, NamedTuple
from uuid import uuid4

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import UploadProblem
from bookreviver.domain.errors import NotFoundError, UploadRejectedError
from bookreviver.domain.ids import ProjectId
from tests.adapters.storage.samples import LARGE_CONTENT, upload

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastapi import UploadFile

    from bookreviver.ports.storage import SourceStore

pytestmark = pytest.mark.anyio

CASE_ARG: str = 'case'
PROJECT_ID: ProjectId = ProjectId(uuid4())
OTHER_PROJECT_ID: ProjectId = ProjectId(uuid4())
OLD_NAME: str = 'old.pdf'
NEW_NAME: str = 'new.pdf'
OLD_CONTENT: bytes = b'old book'
NEW_CONTENT: bytes = b'new book'
PAGE_NAME: str = 'page.png'
UNLIMITED_BYTES: int = 1024**3


class RejectedUploadCase(NamedTuple):
    """An upload the store must refuse, and the problem it must report."""

    files: Sequence[tuple[str | None, bytes]]
    max_bytes: int
    problem: UploadProblem


async def _stage(store: SourceStore, project_id: ProjectId, *files: UploadFile) -> int:
    """Stage the files without a practical size limit."""
    return await store.stage(project_id, files, max_bytes=UNLIMITED_BYTES)


async def _import(store: SourceStore, project_id: ProjectId, *files: UploadFile) -> None:
    """Stage the files and promote them to the project's source."""
    await _stage(store, project_id, *files)
    await store.promote(project_id)


async def _staged(store: SourceStore, project_id: ProjectId) -> dict[str, bytes]:
    """Return the staged files of a project by name."""
    async with store.staged_files(project_id) as paths:
        return {path.name: path.read_bytes() for path in paths}


async def _source(store: SourceStore, project_id: ProjectId) -> dict[str, bytes]:
    """Return the source files of a project by name."""
    async with store.source_files(project_id) as paths:
        return {path.name: path.read_bytes() for path in paths}


class TestStage:
    """Contract of SourceStore.stage()."""

    async def test_streams_files_under_their_base_names(self, fx_source_store: SourceStore) -> None:
        """Verify every file is kept whole under its base name, whatever path the browser sent."""
        files = [upload('scans/page1.png', OLD_CONTENT), upload('C:\\scans\\page2.png', LARGE_CONTENT)]
        total = len(OLD_CONTENT) + len(LARGE_CONTENT)

        # A limit equal to the upload size admits it
        size = await fx_source_store.stage(PROJECT_ID, files, max_bytes=total)

        expect(size == total)
        expect(await _staged(fx_source_store, PROJECT_ID) == {'page1.png': OLD_CONTENT, 'page2.png': LARGE_CONTENT})
        assert_expectations()

    async def test_replaces_previous_staged_upload(self, fx_source_store: SourceStore) -> None:
        """Verify a new upload drops whatever an earlier upload left staged."""
        await _stage(fx_source_store, PROJECT_ID, upload(OLD_NAME, OLD_CONTENT))

        await _stage(fx_source_store, PROJECT_ID, upload(NEW_NAME, NEW_CONTENT))

        assert await _staged(fx_source_store, PROJECT_ID) == {NEW_NAME: NEW_CONTENT}

    async def test_keeps_current_source(self, fx_source_store: SourceStore) -> None:
        """Verify staging leaves the imported source as it is."""
        await _import(fx_source_store, PROJECT_ID, upload(OLD_NAME, OLD_CONTENT))

        await _stage(fx_source_store, PROJECT_ID, upload(NEW_NAME, NEW_CONTENT))

        assert await _source(fx_source_store, PROJECT_ID) == {OLD_NAME: OLD_CONTENT}

    @pytest.mark.parametrize(
        CASE_ARG,
        [
            RejectedUploadCase(
                files=[(PAGE_NAME, OLD_CONTENT), (NEW_NAME, LARGE_CONTENT)],
                max_bytes=len(OLD_CONTENT) + len(LARGE_CONTENT) - 1,
                problem=UploadProblem.TOO_LARGE,
            ),
            RejectedUploadCase(
                files=[(None, OLD_CONTENT)], max_bytes=UNLIMITED_BYTES, problem=UploadProblem.EMPTY_NAME
            ),
            RejectedUploadCase(files=[('', OLD_CONTENT)], max_bytes=UNLIMITED_BYTES, problem=UploadProblem.EMPTY_NAME),
            RejectedUploadCase(
                files=[('scans/..', OLD_CONTENT)], max_bytes=UNLIMITED_BYTES, problem=UploadProblem.EMPTY_NAME
            ),
            RejectedUploadCase(
                files=[('left/page.png', OLD_CONTENT), ('right\\page.png', NEW_CONTENT)],
                max_bytes=UNLIMITED_BYTES,
                problem=UploadProblem.DUPLICATE_NAME,
            ),
        ],
        ids=['too-large', 'no-name', 'empty-name', 'parent-directory', 'same-base-name'],
    )
    async def test_rejected_upload_leaves_nothing_staged(
        self, fx_source_store: SourceStore, case: RejectedUploadCase
    ) -> None:
        """Verify a refused upload reports its problem, keeps nothing it wrote and leaves the source alone."""
        await _import(fx_source_store, PROJECT_ID, upload(OLD_NAME, OLD_CONTENT))
        files = list(starmap(upload, case.files))

        with pytest.raises(UploadRejectedError) as error:
            await fx_source_store.stage(PROJECT_ID, files, max_bytes=case.max_bytes)

        expect(error.value.problem == case.problem)
        expect(await _source(fx_source_store, PROJECT_ID) == {OLD_NAME: OLD_CONTENT})
        assert_expectations()
        with pytest.raises(NotFoundError):
            await _staged(fx_source_store, PROJECT_ID)


class TestPromote:
    """Contract of SourceStore.promote()."""

    async def test_replaces_source_with_staged_upload(self, fx_source_store: SourceStore) -> None:
        """Verify the staged files become the whole source and nothing stays staged."""
        await _import(fx_source_store, PROJECT_ID, upload(OLD_NAME, OLD_CONTENT))
        await _stage(fx_source_store, PROJECT_ID, upload(NEW_NAME, NEW_CONTENT))

        await fx_source_store.promote(PROJECT_ID)

        assert await _source(fx_source_store, PROJECT_ID) == {NEW_NAME: NEW_CONTENT}
        with pytest.raises(NotFoundError):
            await _staged(fx_source_store, PROJECT_ID)

    async def test_first_promotion_creates_source(self, fx_source_store: SourceStore) -> None:
        """Verify promotion works when the project had no source yet."""
        await _stage(fx_source_store, PROJECT_ID, upload(NEW_NAME, NEW_CONTENT))

        await fx_source_store.promote(PROJECT_ID)

        assert await _source(fx_source_store, PROJECT_ID) == {NEW_NAME: NEW_CONTENT}

    async def test_nothing_staged_raises_not_found(self, fx_source_store: SourceStore) -> None:
        """Reject a promotion when no upload is staged, keeping the current source."""
        await _import(fx_source_store, PROJECT_ID, upload(OLD_NAME, OLD_CONTENT))

        with pytest.raises(NotFoundError):
            await fx_source_store.promote(PROJECT_ID)

        assert await _source(fx_source_store, PROJECT_ID) == {OLD_NAME: OLD_CONTENT}


class TestDiscard:
    """Contract of SourceStore.discard()."""

    async def test_removes_staged_upload_and_keeps_source(self, fx_source_store: SourceStore) -> None:
        """Verify discarding drops the staged files and leaves the source untouched."""
        await _import(fx_source_store, PROJECT_ID, upload(OLD_NAME, OLD_CONTENT))
        await _stage(fx_source_store, PROJECT_ID, upload(NEW_NAME, NEW_CONTENT))

        await fx_source_store.discard(PROJECT_ID)

        assert await _source(fx_source_store, PROJECT_ID) == {OLD_NAME: OLD_CONTENT}
        with pytest.raises(NotFoundError):
            await _staged(fx_source_store, PROJECT_ID)

    async def test_nothing_staged_is_not_an_error(self, fx_source_store: SourceStore) -> None:
        """Verify discarding twice succeeds, so a failed import can always clean up."""
        await _stage(fx_source_store, PROJECT_ID, upload(NEW_NAME, NEW_CONTENT))
        await fx_source_store.discard(PROJECT_ID)

        await fx_source_store.discard(PROJECT_ID)

        with pytest.raises(NotFoundError):
            await _staged(fx_source_store, PROJECT_ID)


class TestSourceFiles:
    """Contract of SourceStore.source_files()."""

    async def test_project_without_source_raises_not_found(self, fx_source_store: SourceStore) -> None:
        """Verify a project that never imported has no source files to give."""
        await _stage(fx_source_store, PROJECT_ID, upload(NEW_NAME, NEW_CONTENT))

        with pytest.raises(NotFoundError):
            await _source(fx_source_store, PROJECT_ID)


class TestDeleteProject:
    """Contract of SourceStore.delete_project()."""

    async def test_removes_source_and_staged_upload_of_that_project_only(self, fx_source_store: SourceStore) -> None:
        """Verify the project's source and staged files are gone and another project keeps its own."""
        for project_id in (PROJECT_ID, OTHER_PROJECT_ID):
            await _import(fx_source_store, project_id, upload(OLD_NAME, OLD_CONTENT))
        await _stage(fx_source_store, PROJECT_ID, upload(NEW_NAME, NEW_CONTENT))

        await fx_source_store.delete_project(PROJECT_ID)

        assert await _source(fx_source_store, OTHER_PROJECT_ID) == {OLD_NAME: OLD_CONTENT}
        for read in (_source, _staged):
            with pytest.raises(NotFoundError):
                await read(fx_source_store, PROJECT_ID)
