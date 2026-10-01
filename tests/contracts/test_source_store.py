"""Contract of the SourceStore port, run against every storage backend the application registers."""

import hashlib
from typing import TYPE_CHECKING, NamedTuple
from uuid import uuid4

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import UploadProblem
from bookreviver.domain.errors import ConflictError, NotFoundError, UploadRejectedError
from bookreviver.domain.ids import JobId, ProjectId, SourceId
from bookreviver.domain.values import SourceFile
from tests.helpers.storage import LARGE_CONTENT, upload

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastapi import UploadFile

    from bookreviver.ports.storage import SourceStore

pytestmark = pytest.mark.anyio

CASE_ARG: str = 'case'
NAMES_ARG: str = 'names'
PROJECT_ID: ProjectId = ProjectId(uuid4())
OTHER_PROJECT_ID: ProjectId = ProjectId(uuid4())
JOB_ID: JobId = JobId(uuid4())
OTHER_JOB_ID: JobId = JobId(uuid4())
SOURCE_ID: SourceId = SourceId(uuid4())
OTHER_SOURCE_ID: SourceId = SourceId(uuid4())
OLD_NAME: str = 'old.pdf'
NEW_NAME: str = 'new.pdf'
OLD_CONTENT: bytes = b'old book'
NEW_CONTENT: bytes = b'new book'
PAGE_NAME: str = 'page.png'
FIRST_VOLUME_PAGE: str = 'vol1/001.tif'
SECOND_VOLUME_PAGE: str = 'vol2/001.tif'
UNLIMITED_BYTES: int = 1024**3


class RejectedUploadCase(NamedTuple):
    """An upload the store must refuse, and the problem it must report.

    :ivar files: Name and content of every uploaded file, a name of None meaning none was sent.
    :ivar max_bytes: Size limit the upload is staged with.
    :ivar problem: Upload problem the refusal must carry.
    """

    files: Sequence[tuple[str | None, bytes]]
    max_bytes: int
    problem: UploadProblem


async def _stage(
    store: SourceStore, job_id: JobId, *files: UploadFile, project_id: ProjectId = PROJECT_ID
) -> Sequence[SourceFile]:
    """Stage the files of an import job without a practical size limit.

    :param store: Source store to stage into.
    :type store: SourceStore
    :param job_id: Import job owning the upload.
    :type job_id: JobId
    :param files: Uploaded files.
    :type files: UploadFile
    :param project_id: Project receiving the upload.
    :type project_id: ProjectId
    :returns: The staged files as the store reports them.
    :rtype: Sequence[SourceFile]
    """
    return await store.stage(project_id, job_id, files, max_bytes=UNLIMITED_BYTES)


async def _import(
    store: SourceStore, source_id: SourceId, *files: UploadFile, project_id: ProjectId = PROJECT_ID
) -> None:
    """Stage the files under a new import job and promote all of them to one source.

    :param store: Source store to import into.
    :type store: SourceStore
    :param source_id: Source the files become.
    :type source_id: SourceId
    :param files: Uploaded files.
    :type files: UploadFile
    :param project_id: Project receiving the source.
    :type project_id: ProjectId
    """
    job_id = JobId(uuid4())
    staged = await _stage(store, job_id, *files, project_id=project_id)
    await store.promote(project_id, job_id, source_id, names=[file.name for file in staged])


async def _staged(store: SourceStore, job_id: JobId, *, project_id: ProjectId = PROJECT_ID) -> dict[str, bytes]:
    """Return the files an import job has staged and not promoted, by relative name.

    :param store: Source store to read from.
    :type store: SourceStore
    :param job_id: Import job whose upload is read.
    :type job_id: JobId
    :param project_id: Project owning the upload.
    :type project_id: ProjectId
    :returns: Content of every staged file, by relative name.
    :rtype: dict[str, bytes]
    :raises NotFoundError: If the job has no staged upload.
    """
    async with store.staged_files(project_id, job_id) as paths:
        return {name: path.read_bytes() for name, path in paths.items()}


async def _source(store: SourceStore, source_id: SourceId, *, project_id: ProjectId = PROJECT_ID) -> dict[str, bytes]:
    """Return the files of a source by name.

    :param store: Source store to read from.
    :type store: SourceStore
    :param source_id: Source whose files are read.
    :type source_id: SourceId
    :param project_id: Project owning the source.
    :type project_id: ProjectId
    :returns: Content of every file of the source, by file name.
    :rtype: dict[str, bytes]
    :raises NotFoundError: If the source has no files.
    """
    async with store.source_files(project_id, source_id) as paths:
        return {path.name: path.read_bytes() for path in paths}


class TestStage:
    """Contract of SourceStore.stage()."""

    async def test_streams_files_under_their_relative_paths(self, fx_source_store: SourceStore) -> None:
        """Verify every file is kept whole under its relative path and reported with its size and digest.

        A backslash separates folders as a slash does, since a Windows client may send either.

        :param fx_source_store: Source store of the storage backend under test.
        :type fx_source_store: SourceStore
        """
        files = [upload('scans/page1.png', content=OLD_CONTENT), upload('scans\\page2.png', content=LARGE_CONTENT)]

        # A limit equal to the upload size admits it
        staged = await fx_source_store.stage(PROJECT_ID, JOB_ID, files, max_bytes=len(OLD_CONTENT) + len(LARGE_CONTENT))

        expect(
            list(staged)
            == [
                SourceFile(
                    name='scans/page1.png', size_bytes=len(OLD_CONTENT), sha256=hashlib.sha256(OLD_CONTENT).hexdigest()
                ),
                SourceFile(
                    name='scans/page2.png',
                    size_bytes=len(LARGE_CONTENT),
                    sha256=hashlib.sha256(LARGE_CONTENT).hexdigest(),
                ),
            ]
        )
        expect(
            await _staged(fx_source_store, JOB_ID) == {'scans/page1.png': OLD_CONTENT, 'scans/page2.png': LARGE_CONTENT}
        )
        assert_expectations()

    async def test_files_of_different_folders_with_one_name_are_two_files(self, fx_source_store: SourceStore) -> None:
        """Verify ``vol1/001.tif`` and ``vol2/001.tif`` are staged apart, and the report keeps their upload order.

        :param fx_source_store: Source store of the storage backend under test.
        :type fx_source_store: SourceStore
        """
        staged = await _stage(
            fx_source_store,
            JOB_ID,
            upload(SECOND_VOLUME_PAGE, content=NEW_CONTENT),
            upload(FIRST_VOLUME_PAGE, content=OLD_CONTENT),
        )

        expect([file.name for file in staged] == [SECOND_VOLUME_PAGE, FIRST_VOLUME_PAGE])
        expect(
            await _staged(fx_source_store, JOB_ID) == {FIRST_VOLUME_PAGE: OLD_CONTENT, SECOND_VOLUME_PAGE: NEW_CONTENT}
        )
        assert_expectations()

    async def test_replaces_interrupted_upload_of_the_same_job(self, fx_source_store: SourceStore) -> None:
        """Verify staging again for a job drops whatever the job's earlier upload left.

        :param fx_source_store: Source store of the storage backend under test.
        :type fx_source_store: SourceStore
        """
        await _stage(fx_source_store, JOB_ID, upload(OLD_NAME, content=OLD_CONTENT))

        await _stage(fx_source_store, JOB_ID, upload(NEW_NAME, content=NEW_CONTENT))

        assert await _staged(fx_source_store, JOB_ID) == {NEW_NAME: NEW_CONTENT}

    async def test_keeps_the_upload_of_another_job(self, fx_source_store: SourceStore) -> None:
        """Verify each import job receives its upload apart from the others.

        :param fx_source_store: Source store of the storage backend under test.
        :type fx_source_store: SourceStore
        """
        await _stage(fx_source_store, JOB_ID, upload(OLD_NAME, content=OLD_CONTENT))

        await _stage(fx_source_store, OTHER_JOB_ID, upload(NEW_NAME, content=NEW_CONTENT))

        expect(await _staged(fx_source_store, JOB_ID) == {OLD_NAME: OLD_CONTENT})
        expect(await _staged(fx_source_store, OTHER_JOB_ID) == {NEW_NAME: NEW_CONTENT})
        assert_expectations()

    async def test_accepts_upload_to_project_with_sources(self, fx_source_store: SourceStore) -> None:
        """Verify a project with a source takes another upload, and the source stays as it was.

        :param fx_source_store: Source store of the storage backend under test.
        :type fx_source_store: SourceStore
        """
        await _import(fx_source_store, SOURCE_ID, upload(OLD_NAME, content=OLD_CONTENT))

        await _stage(fx_source_store, JOB_ID, upload(OLD_NAME, content=NEW_CONTENT))

        expect(await _staged(fx_source_store, JOB_ID) == {OLD_NAME: NEW_CONTENT})
        expect(await _source(fx_source_store, SOURCE_ID) == {OLD_NAME: OLD_CONTENT})
        assert_expectations()

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
                files=[('scans/../page.png', OLD_CONTENT)], max_bytes=UNLIMITED_BYTES, problem=UploadProblem.UNSAFE_PATH
            ),
            RejectedUploadCase(
                files=[('/etc/page.png', OLD_CONTENT)], max_bytes=UNLIMITED_BYTES, problem=UploadProblem.UNSAFE_PATH
            ),
            RejectedUploadCase(
                files=[('C:\\scans\\page.png', OLD_CONTENT)],
                max_bytes=UNLIMITED_BYTES,
                problem=UploadProblem.UNSAFE_PATH,
            ),
            RejectedUploadCase(
                files=[('scans//page.png', OLD_CONTENT)], max_bytes=UNLIMITED_BYTES, problem=UploadProblem.UNSAFE_PATH
            ),
            RejectedUploadCase(
                files=[('left/page.png', OLD_CONTENT), ('left\\page.png', NEW_CONTENT)],
                max_bytes=UNLIMITED_BYTES,
                problem=UploadProblem.DUPLICATE_NAME,
            ),
            # A case-insensitive file system would store both under one name
            RejectedUploadCase(
                files=[('Vol1/Page.JPG', OLD_CONTENT), ('vol1/page.jpg', NEW_CONTENT)],
                max_bytes=UNLIMITED_BYTES,
                problem=UploadProblem.DUPLICATE_NAME,
            ),
            # A path cannot be a file and a folder, whichever comes first
            RejectedUploadCase(
                files=[('vol1', OLD_CONTENT), ('vol1/page.png', NEW_CONTENT)],
                max_bytes=UNLIMITED_BYTES,
                problem=UploadProblem.DUPLICATE_NAME,
            ),
            RejectedUploadCase(
                files=[('vol1/page.png', OLD_CONTENT), ('VOL1', NEW_CONTENT)],
                max_bytes=UNLIMITED_BYTES,
                problem=UploadProblem.DUPLICATE_NAME,
            ),
        ],
        ids=[
            'too-large',
            'no-name',
            'empty-name',
            'parent-directory-as-name',
            'parent-directory-in-path',
            'absolute-path',
            'drive-letter',
            'empty-segment',
            'same-path',
            'same-path-other-case',
            'file-then-folder',
            'folder-then-file',
        ],
    )
    async def test_rejected_upload_leaves_nothing_staged(
        self, fx_source_store: SourceStore, case: RejectedUploadCase
    ) -> None:
        """Verify a refused upload reports its problem and keeps nothing it wrote.

        :param fx_source_store: Source store of the storage backend under test.
        :type fx_source_store: SourceStore
        :param case: An upload the store must refuse, and the problem it must report.
        :type case: RejectedUploadCase
        """
        files = [upload(name, content=content) for name, content in case.files]

        with pytest.raises(UploadRejectedError) as error:
            await fx_source_store.stage(PROJECT_ID, JOB_ID, files, max_bytes=case.max_bytes)

        assert error.value.problem == case.problem
        with pytest.raises(NotFoundError):
            await _staged(fx_source_store, JOB_ID)


class TestPromote:
    """Contract of SourceStore.promote()."""

    async def test_each_source_takes_only_its_own_files(self, fx_source_store: SourceStore) -> None:
        """Verify an upload of two files becomes two sources, and nothing stays staged.

        :param fx_source_store: Source store of the storage backend under test.
        :type fx_source_store: SourceStore
        """
        await _stage(
            fx_source_store, JOB_ID, upload(OLD_NAME, content=OLD_CONTENT), upload(NEW_NAME, content=NEW_CONTENT)
        )

        await fx_source_store.promote(PROJECT_ID, JOB_ID, SOURCE_ID, names=[OLD_NAME])
        await fx_source_store.promote(PROJECT_ID, JOB_ID, OTHER_SOURCE_ID, names=[NEW_NAME])

        expect(await _source(fx_source_store, SOURCE_ID) == {OLD_NAME: OLD_CONTENT})
        expect(await _source(fx_source_store, OTHER_SOURCE_ID) == {NEW_NAME: NEW_CONTENT})
        expect(await _staged(fx_source_store, JOB_ID) == {})
        assert_expectations()

    async def test_source_takes_several_files(self, fx_source_store: SourceStore) -> None:
        """Verify one source can hold several files, as an indirect DjVu document does.

        :param fx_source_store: Source store of the storage backend under test.
        :type fx_source_store: SourceStore
        """
        await _stage(
            fx_source_store, JOB_ID, upload(OLD_NAME, content=OLD_CONTENT), upload(NEW_NAME, content=NEW_CONTENT)
        )

        await fx_source_store.promote(PROJECT_ID, JOB_ID, SOURCE_ID, names=[OLD_NAME, NEW_NAME])

        assert await _source(fx_source_store, SOURCE_ID) == {OLD_NAME: OLD_CONTENT, NEW_NAME: NEW_CONTENT}

    async def test_source_keeps_a_file_under_its_base_name_and_the_others_stay_staged(
        self, fx_source_store: SourceStore
    ) -> None:
        """Verify a file of a folder is stored under its base name, and the same name of another folder stays staged.

        :param fx_source_store: Source store of the storage backend under test.
        :type fx_source_store: SourceStore
        """
        await _stage(
            fx_source_store,
            JOB_ID,
            upload(FIRST_VOLUME_PAGE, content=OLD_CONTENT),
            upload(SECOND_VOLUME_PAGE, content=NEW_CONTENT),
        )

        await fx_source_store.promote(PROJECT_ID, JOB_ID, SOURCE_ID, names=[FIRST_VOLUME_PAGE])
        await fx_source_store.promote(PROJECT_ID, JOB_ID, OTHER_SOURCE_ID, names=[SECOND_VOLUME_PAGE])

        expect(await _source(fx_source_store, SOURCE_ID) == {'001.tif': OLD_CONTENT})
        expect(await _source(fx_source_store, OTHER_SOURCE_ID) == {'001.tif': NEW_CONTENT})
        expect(await _staged(fx_source_store, JOB_ID) == {})
        assert_expectations()

    async def test_two_files_ending_in_one_name_raise_conflict_and_move_nothing(
        self, fx_source_store: SourceStore
    ) -> None:
        """Reject a source whose files would share a name in its directory, keeping every file staged.

        :param fx_source_store: Source store of the storage backend under test.
        :type fx_source_store: SourceStore
        """
        await _stage(
            fx_source_store,
            JOB_ID,
            upload(FIRST_VOLUME_PAGE, content=OLD_CONTENT),
            upload(SECOND_VOLUME_PAGE, content=NEW_CONTENT),
        )

        with pytest.raises(ConflictError):
            await fx_source_store.promote(PROJECT_ID, JOB_ID, SOURCE_ID, names=[FIRST_VOLUME_PAGE, SECOND_VOLUME_PAGE])

        expect(
            await _staged(fx_source_store, JOB_ID) == {FIRST_VOLUME_PAGE: OLD_CONTENT, SECOND_VOLUME_PAGE: NEW_CONTENT}
        )
        with pytest.raises(NotFoundError):
            await _source(fx_source_store, SOURCE_ID)
        assert_expectations()

    @pytest.mark.parametrize(NAMES_ARG, [(PAGE_NAME,), (OLD_NAME, PAGE_NAME), ('../escape.pdf',)])
    async def test_name_not_staged_raises_not_found_and_moves_nothing(
        self, fx_source_store: SourceStore, names: Sequence[str]
    ) -> None:
        """Reject a promotion naming a file the job has not staged, keeping every staged file where it was.

        :param fx_source_store: Source store of the storage backend under test.
        :type fx_source_store: SourceStore
        :param names: Names of the files to promote, at least one of them not staged.
        :type names: Sequence[str]
        """
        await _stage(fx_source_store, JOB_ID, upload(OLD_NAME, content=OLD_CONTENT))

        with pytest.raises(NotFoundError):
            await fx_source_store.promote(PROJECT_ID, JOB_ID, SOURCE_ID, names=names)

        assert await _staged(fx_source_store, JOB_ID) == {OLD_NAME: OLD_CONTENT}
        with pytest.raises(NotFoundError):
            await _source(fx_source_store, SOURCE_ID)

    async def test_job_without_upload_raises_not_found(self, fx_source_store: SourceStore) -> None:
        """Reject a promotion from a job that has staged nothing.

        :param fx_source_store: Source store of the storage backend under test.
        :type fx_source_store: SourceStore
        """
        with pytest.raises(NotFoundError):
            await fx_source_store.promote(PROJECT_ID, JOB_ID, SOURCE_ID, names=[OLD_NAME])

    async def test_existing_source_is_never_replaced(self, fx_source_store: SourceStore) -> None:
        """Verify a source keeps its files, and the refused files stay staged for another attempt.

        :param fx_source_store: Source store of the storage backend under test.
        :type fx_source_store: SourceStore
        """
        await _import(fx_source_store, SOURCE_ID, upload(OLD_NAME, content=OLD_CONTENT))
        await _stage(fx_source_store, JOB_ID, upload(NEW_NAME, content=NEW_CONTENT))

        with pytest.raises(ConflictError):
            await fx_source_store.promote(PROJECT_ID, JOB_ID, SOURCE_ID, names=[NEW_NAME])

        expect(await _source(fx_source_store, SOURCE_ID) == {OLD_NAME: OLD_CONTENT})
        expect(await _staged(fx_source_store, JOB_ID) == {NEW_NAME: NEW_CONTENT})
        assert_expectations()

    async def test_refused_promotion_leaves_a_file_in_its_folder(self, fx_source_store: SourceStore) -> None:
        """Verify the files of a refused promotion stay staged under the relative paths they had.

        :param fx_source_store: Source store of the storage backend under test.
        :type fx_source_store: SourceStore
        """
        await _import(fx_source_store, SOURCE_ID, upload(OLD_NAME, content=OLD_CONTENT))
        await _stage(fx_source_store, JOB_ID, upload(FIRST_VOLUME_PAGE, content=NEW_CONTENT))

        with pytest.raises(ConflictError):
            await fx_source_store.promote(PROJECT_ID, JOB_ID, SOURCE_ID, names=[FIRST_VOLUME_PAGE])

        assert await _staged(fx_source_store, JOB_ID) == {FIRST_VOLUME_PAGE: NEW_CONTENT}

    async def test_no_names_raises_value_error(self, fx_source_store: SourceStore) -> None:
        """Reject a source without files, which a caller can only ask for by mistake.

        :param fx_source_store: Source store of the storage backend under test.
        :type fx_source_store: SourceStore
        """
        await _stage(fx_source_store, JOB_ID, upload(OLD_NAME, content=OLD_CONTENT))

        with pytest.raises(ValueError, match='needs at least one file'):
            await fx_source_store.promote(PROJECT_ID, JOB_ID, SOURCE_ID, names=[])


class TestDiscard:
    """Contract of SourceStore.discard()."""

    async def test_removes_the_upload_of_that_job_only(self, fx_source_store: SourceStore) -> None:
        """Verify a failed import drops its own upload and leaves another job's upload alone.

        :param fx_source_store: Source store of the storage backend under test.
        :type fx_source_store: SourceStore
        """
        await _stage(fx_source_store, JOB_ID, upload(OLD_NAME, content=OLD_CONTENT))
        await _stage(fx_source_store, OTHER_JOB_ID, upload(NEW_NAME, content=NEW_CONTENT))

        await fx_source_store.discard(PROJECT_ID, JOB_ID)

        assert await _staged(fx_source_store, OTHER_JOB_ID) == {NEW_NAME: NEW_CONTENT}
        with pytest.raises(NotFoundError):
            await _staged(fx_source_store, JOB_ID)

    async def test_nothing_staged_is_not_an_error(self, fx_source_store: SourceStore) -> None:
        """Verify discarding twice succeeds, so a failed import can always clean up.

        :param fx_source_store: Source store of the storage backend under test.
        :type fx_source_store: SourceStore
        """
        await _stage(fx_source_store, JOB_ID, upload(NEW_NAME, content=NEW_CONTENT))
        await fx_source_store.discard(PROJECT_ID, JOB_ID)

        await fx_source_store.discard(PROJECT_ID, JOB_ID)

        with pytest.raises(NotFoundError):
            await _staged(fx_source_store, JOB_ID)


class TestSourceFiles:
    """Contract of SourceStore.source_files()."""

    async def test_source_never_promoted_raises_not_found(self, fx_source_store: SourceStore) -> None:
        """Verify staged files are not the files of any source until they are promoted.

        :param fx_source_store: Source store of the storage backend under test.
        :type fx_source_store: SourceStore
        """
        await _stage(fx_source_store, JOB_ID, upload(NEW_NAME, content=NEW_CONTENT))

        with pytest.raises(NotFoundError):
            await _source(fx_source_store, SOURCE_ID)


class TestDeleteSource:
    """Contract of SourceStore.delete_source()."""

    async def test_removes_that_source_only(self, fx_source_store: SourceStore) -> None:
        """Verify the deleted source's files are gone and another source of the project keeps its own.

        :param fx_source_store: Source store of the storage backend under test.
        :type fx_source_store: SourceStore
        """
        await _import(fx_source_store, SOURCE_ID, upload(OLD_NAME, content=OLD_CONTENT))
        await _import(fx_source_store, OTHER_SOURCE_ID, upload(NEW_NAME, content=NEW_CONTENT))

        await fx_source_store.delete_source(PROJECT_ID, SOURCE_ID)

        assert await _source(fx_source_store, OTHER_SOURCE_ID) == {NEW_NAME: NEW_CONTENT}
        with pytest.raises(NotFoundError):
            await _source(fx_source_store, SOURCE_ID)

    async def test_missing_source_is_not_an_error(self, fx_source_store: SourceStore) -> None:
        """Verify deleting a source twice succeeds, so a failed deletion can be repeated.

        :param fx_source_store: Source store of the storage backend under test.
        :type fx_source_store: SourceStore
        """
        await _import(fx_source_store, SOURCE_ID, upload(OLD_NAME, content=OLD_CONTENT))
        await fx_source_store.delete_source(PROJECT_ID, SOURCE_ID)

        await fx_source_store.delete_source(PROJECT_ID, SOURCE_ID)

        with pytest.raises(NotFoundError):
            await _source(fx_source_store, SOURCE_ID)


class TestDeleteProject:
    """Contract of SourceStore.delete_project()."""

    async def test_removes_sources_and_uploads_of_that_project_only(self, fx_source_store: SourceStore) -> None:
        """Verify the project's sources and staged upload are gone and another project keeps its source.

        :param fx_source_store: Source store of the storage backend under test.
        :type fx_source_store: SourceStore
        """
        await _import(fx_source_store, SOURCE_ID, upload(OLD_NAME, content=OLD_CONTENT))
        await _stage(fx_source_store, JOB_ID, upload(NEW_NAME, content=NEW_CONTENT))
        await _import(
            fx_source_store, OTHER_SOURCE_ID, upload(OLD_NAME, content=OLD_CONTENT), project_id=OTHER_PROJECT_ID
        )

        await fx_source_store.delete_project(PROJECT_ID)

        assert await _source(fx_source_store, OTHER_SOURCE_ID, project_id=OTHER_PROJECT_ID) == {OLD_NAME: OLD_CONTENT}
        with pytest.raises(NotFoundError):
            await _source(fx_source_store, SOURCE_ID)
        with pytest.raises(NotFoundError):
            await _staged(fx_source_store, JOB_ID)

    async def test_project_without_files_is_not_an_error(self, fx_source_store: SourceStore) -> None:
        """Verify deleting a project that never received an upload succeeds.

        :param fx_source_store: Source store of the storage backend under test.
        :type fx_source_store: SourceStore
        """
        await fx_source_store.delete_project(PROJECT_ID)

        with pytest.raises(NotFoundError):
            await _source(fx_source_store, SOURCE_ID)
