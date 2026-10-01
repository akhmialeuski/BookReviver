"""Manual edits of a page, which a processor reads as an input beside its parameters.

A frame, an angle, a split line or the mask of an eraser is stored apart from the parameters of a step, on the page,
the stage and the processor that reads it, which a ``PageEditKey`` names. The hash of the edit, taken over its shape in
canonical JSON and over the SHA-256 digest of its mask, joins the identifier of the versions that read it, so an edit
behaves like a parameter: a changed line gives a new version, and the old line finds the old version in the cache. A
mask is stored under the key of its edit, in a directory of the hash, so a later mask never replaces an earlier one.

An edit computes nothing. It marks the stage of its page stale, and a run of the stage picks it up like any other.

A mask arrives as an upload. It is written to a scratch key while its digest is worked out, since its final key holds
the hash of the whole edit, and then copied to that key and the scratch removed.
"""

import hashlib
from contextlib import suppress
from typing import TYPE_CHECKING
from uuid import uuid4

import anyio

from bookreviver.domain.entities import PageEdit
from bookreviver.domain.enums import EditorKind, Rendition
from bookreviver.domain.errors import ConflictError, InvalidParametersError, NotFoundError
from bookreviver.domain.ids import StorageKey
from bookreviver.domain.keys import ProjectKeys
from bookreviver.services.projects import owned_project

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.entities import Actor, Page
    from bookreviver.domain.enums import Stage
    from bookreviver.domain.ids import PageId, ProjectId
    from bookreviver.domain.values import NewPageEdit, PageEditKey
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.processing import ProcessorCatalog
    from bookreviver.ports.runtime import Clock
    from bookreviver.ports.storage import AssetStore, IncomingFile
    from bookreviver.services.stage_records import StageRecords

UNKNOWN_PROCESSOR: str = 'There is no processor {key}.'
WRONG_STAGE: str = 'The processor {key} belongs to the {actual} stage, not to the {expected} stage.'
WRONG_EDITOR: str = 'The processor {key} reads an edit of the {expected} editor, not of the {actual} editor.'
NEEDS_GEOMETRY: str = 'An edit of the {editor} editor needs its shape.'
NEEDS_NO_GEOMETRY: str = 'An edit of the {editor} editor draws no shape.'
NEEDS_NO_MASK: str = 'The {editor} editor keeps no mask.'
NEEDS_MASK: str = 'An edit of the {editor} editor needs its mask.'
# Editors whose edit is a mask the user painted, and not a shape the user drew
MASK_EDITORS: frozenset[EditorKind] = frozenset({EditorKind.BRUSH_MASK})


class EditService:
    """Saves, lists and deletes the manual edits of the pages of the acting account's projects."""

    # Bytes of a mask read from an upload at a time
    UPLOAD_CHUNK_BYTES: int = 1024 * 1024

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        assets: AssetStore,
        catalogue: ProcessorCatalog,
        records: StageRecords,
        clock: Clock,
    ) -> None:
        """Work over the ports of one request.

        :param uow: Unit of work of the request, whose commit ends every changing use case.
        :type uow: UnitOfWork
        :param assets: Store of the derived files, where masks are kept.
        :type assets: AssetStore
        :param catalogue: The processors the application can run, which say what edit each reads.
        :type catalogue: ProcessorCatalog
        :param records: Writer of the stage records, which an edit marks stale.
        :type records: StageRecords
        :param clock: Clock stamping the edits.
        :type clock: Clock
        """
        self._uow = uow
        self._assets = assets
        self._catalogue = catalogue
        self._records = records
        self._clock = clock

    async def save(
        self, actor: Actor, project_id: ProjectId, key: PageEditKey, edit: NewPageEdit, mask: IncomingFile | None
    ) -> PageEdit:
        """Store the edit a processor reads, replacing the one it read before, and mark the stage stale.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param key: The page, the stage and the processor that reads the edit.
        :type key: PageEditKey
        :param edit: The shape the user drew and the editor that drew it.
        :type edit: NewPageEdit
        :param mask: The mask the user painted, or None for an edit without one.
        :type mask: IncomingFile | None
        :returns: The edit as stored, with its hash.
        :rtype: PageEdit
        :raises NotFoundError: If the actor has no such project, the project has no such page, or there is no such
                               processor.
        :raises InvalidParametersError: If the processor is of another stage or reads another editor, or the edit lacks
                                        its shape or its mask or has one it should not.
        """
        await owned_project(self._uow.projects, actor, project_id)
        await self._page(project_id, key.page_id)
        self._check(key, edit, has_mask=mask is not None)
        keys = ProjectKeys(project_id)
        mask_digest, scratch = (None, None) if mask is None else await self._receive(keys, key, mask)
        edit_hash = PageEdit.hash_of(edit.geometry, mask_digest)
        mask_key = None
        if scratch is not None:
            directory = keys.page_edit(key.page_id, key.processor_key, edit_hash)
            mask_key = StorageKey(f'{directory}{keys.SEPARATOR}{Rendition.MASK}')
            await self._keep(scratch, mask_key)
        stored = PageEdit(
            page_id=key.page_id,
            stage=key.stage,
            processor_key=key.processor_key,
            kind=edit.kind,
            geometry=edit.geometry,
            mask_key=mask_key,
            edit_hash=edit_hash,
            updated_at=self._clock.now(),
        )
        await self._uow.page_edits.save(stored)
        stale = await self._records.mark_stale(key.page_id, key.stage)
        await self._uow.commit()
        await self._records.announce(project_id, stale)
        return stored

    async def list(self, actor: Actor, project_id: ProjectId, page_id: PageId, stage: Stage) -> Sequence[PageEdit]:
        """List the edits of one stage of a page, by processor.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :param stage: The stage whose edits are listed.
        :type stage: Stage
        :returns: The edits of the stage.
        :rtype: Sequence[PageEdit]
        :raises NotFoundError: If the actor has no such project, or the project has no such page.
        """
        await owned_project(self._uow.projects, actor, project_id)
        await self._page(project_id, page_id)
        return await self._uow.page_edits.list_for_page(page_id, stage)

    async def delete(self, actor: Actor, project_id: ProjectId, key: PageEditKey) -> None:
        """Delete the edit a processor reads, and mark the stage stale.

        The files of its mask stay, since an edit is the input of the user and an equal edit stored again finds them.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param key: The page, the stage and the processor that reads the edit.
        :type key: PageEditKey
        :raises NotFoundError: If the actor has no such project, the project has no such page, or the processor has no
                               edit there.
        """
        await owned_project(self._uow.projects, actor, project_id)
        await self._page(project_id, key.page_id)
        await self._uow.page_edits.delete(key)
        stale = await self._records.mark_stale(key.page_id, key.stage)
        await self._uow.commit()
        await self._records.announce(project_id, stale)

    async def _page(self, project_id: ProjectId, page_id: PageId) -> Page:
        """Return a page of the project.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :returns: The page.
        :rtype: Page
        :raises NotFoundError: If the page does not exist or belongs to another project.
        """
        page = await self._uow.pages.get(page_id)
        if page.project_id != project_id:
            raise NotFoundError(page_id)
        return page

    def _check(self, key: PageEditKey, edit: NewPageEdit, *, has_mask: bool) -> None:
        """Check that the edit is the one the processor reads.

        :param key: The page, the stage and the processor that reads the edit.
        :type key: PageEditKey
        :param edit: The edit.
        :type edit: NewPageEdit
        :param has_mask: Whether a mask came with the edit.
        :type has_mask: bool
        :raises NotFoundError: If there is no such processor.
        :raises InvalidParametersError: If the processor is of another stage or reads another editor, or the edit lacks
                                        its shape or its mask or has one it should not.
        """
        spec = self._catalogue.get(key.processor_key).spec
        if spec.stage is not key.stage:
            err_msg = WRONG_STAGE.format(key=key.processor_key, actual=spec.stage.label, expected=key.stage.label)
            raise InvalidParametersError(err_msg)
        if spec.editor is not edit.kind:
            err_msg = WRONG_EDITOR.format(key=key.processor_key, expected=spec.editor.label, actual=edit.kind.label)
            raise InvalidParametersError(err_msg)
        masked = edit.kind in MASK_EDITORS
        if masked and not has_mask:
            raise InvalidParametersError(NEEDS_MASK.format(editor=edit.kind.label))
        if not masked and has_mask:
            raise InvalidParametersError(NEEDS_NO_MASK.format(editor=edit.kind.label))
        if not masked and edit.geometry is None:
            raise InvalidParametersError(NEEDS_GEOMETRY.format(editor=edit.kind.label))
        if masked and edit.geometry is not None:
            raise InvalidParametersError(NEEDS_NO_GEOMETRY.format(editor=edit.kind.label))

    async def _receive(self, keys: ProjectKeys, key: PageEditKey, mask: IncomingFile) -> tuple[str, StorageKey]:
        """Write an uploaded mask to a scratch key, and work out its digest while it streams in.

        :param keys: Keys of the project.
        :type keys: ProjectKeys
        :param key: The page, the stage and the processor that reads the edit.
        :type key: PageEditKey
        :param mask: The upload.
        :type mask: IncomingFile
        :returns: The SHA-256 digest of the mask and the scratch key it was written to.
        :rtype: tuple[str, StorageKey]
        """
        scratch = keys.page_edit(key.page_id, key.processor_key, f'upload-{uuid4().hex}')
        digest = hashlib.sha256()
        async with self._assets.writable(scratch) as target, await anyio.open_file(target, 'wb') as out:
            while chunk := await mask.read(self.UPLOAD_CHUNK_BYTES):
                digest.update(chunk)
                await out.write(chunk)
        return digest.hexdigest(), scratch

    async def _keep(self, scratch: StorageKey, target: StorageKey) -> None:
        """Move a received mask to its final key, unless an equal edit stored it there before.

        :param scratch: Key the mask was written to.
        :type scratch: StorageKey
        :param target: Final key, which holds the hash of the edit.
        :type target: StorageKey
        """
        # A conflict means the same edit was stored before, and a stored file is never replaced
        with suppress(ConflictError):
            await self._assets.copy(scratch, target)
        await self._assets.delete_prefix(scratch)
