"""Manual edits of a page, which a processor reads as an input beside its parameters.

A frame, an angle, a split line or the mask of an eraser is stored apart from the parameters of a step, on the page,
the stage and the step of a recipe that reads it, which a ``PageStepKey`` names, so two steps of one processor keep
their own edits. The edit is the manual layer of the ``PageStepState`` of the step on the page, which also holds the
settings of the page, so saving or deleting an edit leaves the settings as they are. The hash of the edit, taken over
its shape in canonical JSON and over the SHA-256 digest of its mask, joins the identifier of the versions that read it,
so an edit behaves like a parameter: a changed line gives a new version, and the old line finds the old version in the
cache. A mask is stored under the key of its edit, in a directory of the hash, so a later mask never replaces an
earlier one.

An edit computes nothing. It marks the stage of its page stale, and a run of the stage picks it up like any other.
Saving and deleting an edit write the manual layer of the step to the history of the page, with the edit before and
after as snapshots, so an undo can put either back. The rows of an edit are written by the work of one
``change_book``, with the stale marks.

A mask arrives as an upload. It is written to a scratch key while its digest is worked out, since its final key holds
the hash of the whole edit, and then copied to that key and the scratch removed, all before the transaction opens.
"""

import hashlib
from contextlib import suppress
from typing import TYPE_CHECKING
from uuid import uuid4

import anyio
from attrs import evolve

from bookreviver.domain.entities import PageEdit, PageHistoryEntry, PageStepState
from bookreviver.domain.enums import ChangeSource, EditorKind, Rendition, StepLayer
from bookreviver.domain.errors import ConflictError, InvalidParametersError, NotFoundError
from bookreviver.domain.ids import StorageKey
from bookreviver.domain.keys import BookKeys
from bookreviver.services.library import Library
from bookreviver.services.recipes import find_step

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.entities import Actor, BookRecord
    from bookreviver.domain.enums import Stage
    from bookreviver.domain.ids import BookId, PageId
    from bookreviver.domain.values import NewPageEdit, PageStepKey, ProcessorSpec
    from bookreviver.ports.persistence import BookRepositories, UnitOfWork
    from bookreviver.ports.processing import ProcessorCatalog
    from bookreviver.ports.runtime import Clock
    from bookreviver.ports.storage import AssetStore, IncomingFile
    from bookreviver.services.stage_records import StageRecords

WRONG_EDITOR: str = 'The processor {key} reads an edit of the {expected} editor, not of the {actual} editor.'
NEEDS_GEOMETRY: str = 'An edit of the {editor} editor needs its shape.'
NEEDS_NO_MASK: str = 'The {editor} editor keeps no mask.'
NEEDS_MASK: str = 'An edit of the {editor} editor needs its mask.'
# Editors whose edit is a mask the user painted, which may come with the strokes it was painted from, and not a shape
# the user drew
MASK_EDITORS: frozenset[EditorKind] = frozenset({EditorKind.BRUSH_MASK})


class EditService:
    """Saves, lists and deletes the manual edits of the pages of the acting account's books.

    :ivar UPLOAD_CHUNK_BYTES: Bytes of a mask read from an upload at a time.
    """

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

        :param uow: Unit of work of the request, whose ``change_book`` ends every changing use case.
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
        self._library = Library(uow=uow)
        self._assets = assets
        self._catalogue = catalogue
        self._records = records
        self._clock = clock

    async def save(
        self, actor: Actor, book_id: BookId, key: PageStepKey, edit: NewPageEdit, mask: IncomingFile | None
    ) -> PageEdit:
        """Store the edit a step reads, replacing the one it read before, and mark the stage stale.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param book_id: Identifier of the book.
        :type book_id: BookId
        :param key: The page, the stage and the step that reads the edit.
        :type key: PageStepKey
        :param edit: The shape the user drew and the editor that drew it.
        :type edit: NewPageEdit
        :param mask: The mask the user painted, or None for an edit without one.
        :type mask: IncomingFile | None
        :returns: The edit as stored, with its hash.
        :rtype: PageEdit
        :raises NotFoundError: If the actor has no such book, the book has no such page, or no recipe of the stage
                               has such a step.
        :raises InvalidParametersError: If the processor of the step reads another editor, or the edit lacks its shape
                                        or its mask or has one it should not.
        """
        await self._library.open(actor, book_id)

        def check(repos: BookRepositories) -> None:
            """Require the book, the page and the edit to be as the request says.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :raises NotFoundError: If the book has no such page, or no recipe of the
                                   stage has such a step.
            :raises InvalidParametersError: If the processor of the step reads another editor, or the edit lacks its
                                            shape or its mask or has one it should not.
            """
            repos.pages.get(key.page_id)
            self._check(repos, key, edit, has_mask=mask is not None)

        # The edit is checked before its mask is written, so a refused request leaves no file, and checked again in the
        # change, since the page or the recipe may have changed while the mask streamed in
        await self._uow.read_book(book_id, check)
        keys = BookKeys(book_id)
        mask_digest, scratch = (None, None) if mask is None else await self._receive(keys, key, mask)
        edit_hash = PageEdit.hash_of(edit.geometry, mask_digest)
        mask_key = None
        if scratch is not None:
            directory = keys.page_edit(key.page_id, key.step_id, edit_hash)
            mask_key = StorageKey(f'{directory}{keys.SEPARATOR}{Rendition.MASK}')
            await self._keep(scratch, mask_key)
        stored = PageEdit(
            page_id=key.page_id,
            stage=key.stage,
            step_id=key.step_id,
            kind=edit.kind,
            geometry=edit.geometry,
            mask_key=mask_key,
            edit_hash=edit_hash,
            updated_at=self._clock.now(),
        )

        def work(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Check the request again, write the edit, and mark the stage stale.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it.
            :type _book_record: BookRecord
            :raises NotFoundError: If the book has no such page, or no recipe of the
                                   stage has such a step.
            :raises InvalidParametersError: If the processor of the step reads another editor, or the edit lacks its
                                            shape or its mask or has one it should not.
            """
            repos.pages.get(key.page_id)
            self._check(repos, key, edit, has_mask=mask is not None)
            state = repos.page_step_states.find(key) or PageStepState(
                page_id=key.page_id, stage=key.stage, step_id=key.step_id, updated_at=stored.updated_at
            )
            self._store(repos, state, evolve(state, edit=stored, updated_at=stored.updated_at))
            self._records.mark_stale(repos, key.page_id, key.stage)

        # A mask stays when the change fails, since its key holds the hash of the edit and an equal edit finds it there
        await self._uow.change_book(book_id, work)
        return stored

    async def list(self, actor: Actor, book_id: BookId, page_id: PageId, stage: Stage) -> Sequence[PageEdit]:
        """List the edits of one stage of a page, by step.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param book_id: Identifier of the book.
        :type book_id: BookId
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :param stage: The stage whose edits are listed.
        :type stage: Stage
        :returns: The edits of the stage.
        :rtype: Sequence[PageEdit]
        :raises NotFoundError: If the actor has no such book, or the book has no such page.
        """
        await self._library.open(actor, book_id)

        def work(repos: BookRepositories) -> list[PageEdit]:
            """Read the edits of the stage of the page.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: The edits of the stage.
            :rtype: list[PageEdit]
            :raises NotFoundError: If the book has no such page.
            """
            repos.pages.get(page_id)
            states = repos.page_step_states.list_for_page(page_id, stage)
            return [state.edit for state in states if state.edit is not None]

        return await self._uow.read_book(book_id, work)

    async def delete(self, actor: Actor, book_id: BookId, key: PageStepKey) -> None:
        """Delete the edit a step reads, and mark the stage stale.

        The files of its mask stay, since an edit is the input of the user and an equal edit stored again finds them.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param book_id: Identifier of the book.
        :type book_id: BookId
        :param key: The page, the stage and the step that reads the edit.
        :type key: PageStepKey
        :raises NotFoundError: If the actor has no such book, the book has no such page, or the step has no edit
                               there.
        """
        await self._library.open(actor, book_id)

        def work(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Delete the edit from the state of the step, and mark the stage stale.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it.
            :type _book_record: BookRecord
            :raises NotFoundError: If the book has no such page, or the step has no
                                   edit there.
            """
            repos.pages.get(key.page_id)
            state = repos.page_step_states.find(key)
            if state is None or state.edit is None:
                raise NotFoundError(key)
            self._store(repos, state, evolve(state, edit=None, updated_at=self._clock.now()))
            self._records.mark_stale(repos, key.page_id, key.stage)

        await self._uow.change_book(book_id, work)

    @staticmethod
    def _store(repos: BookRepositories, state: PageStepState, changed: PageStepState) -> None:
        """Write the state with the edit saved or deleted, and the change of the manual layer to the history.

        It writes through the repositories of the work of its caller. A state with neither a setting nor an edit left
        is deleted. An edit equal to the stored one writes no change.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param state: The state as it is stored, or an empty one.
        :type state: PageStepState
        :param changed: The state after the edit was saved or deleted.
        :type changed: PageStepState
        """
        if changed.is_empty:
            repos.page_step_states.delete(state.key)
        else:
            repos.page_step_states.save(changed)
        change = PageHistoryEntry.between(state, changed, StepLayer.HAND, ChangeSource.USER)
        if change.before != change.after:
            repos.page_history.add(change)

    def _spec(self, repos: BookRepositories, key: PageStepKey) -> ProcessorSpec:
        """Find the spec of the processor of a step, by the step in the recipes of the stage.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param key: The page, the stage and the step that reads the edit.
        :type key: PageStepKey
        :returns: What the processor of the step says about itself.
        :rtype: ProcessorSpec
        :raises NotFoundError: If no recipe of the stage has the step, or its processor is not installed.
        """
        step = find_step(repos.recipes, key)
        return self._catalogue.get(step.processor_key).spec

    def _check(self, repos: BookRepositories, key: PageStepKey, edit: NewPageEdit, *, has_mask: bool) -> None:
        """Check that the edit is the one the processor of the step reads.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param key: The page, the stage and the step that reads the edit.
        :type key: PageStepKey
        :param edit: The edit.
        :type edit: NewPageEdit
        :param has_mask: Whether a mask came with the edit.
        :type has_mask: bool
        :raises NotFoundError: If no recipe of the stage has such a step.
        :raises InvalidParametersError: If the processor of the step reads another editor, or the edit lacks its shape
                                        or its mask or has one it should not.
        """
        spec = self._spec(repos, key)
        if spec.editor is not edit.kind:
            err_msg = WRONG_EDITOR.format(key=spec.key, expected=spec.editor.label, actual=edit.kind.label)
            raise InvalidParametersError(err_msg)
        masked = edit.kind in MASK_EDITORS
        if masked and not has_mask:
            raise InvalidParametersError(NEEDS_MASK.format(editor=edit.kind.label))
        if not masked and has_mask:
            raise InvalidParametersError(NEEDS_NO_MASK.format(editor=edit.kind.label))
        if not masked and edit.geometry is None:
            raise InvalidParametersError(NEEDS_GEOMETRY.format(editor=edit.kind.label))

    async def _receive(self, keys: BookKeys, key: PageStepKey, mask: IncomingFile) -> tuple[str, StorageKey]:
        """Write an uploaded mask to a scratch key, and work out its digest while it streams in.

        :param keys: Keys of the book.
        :type keys: BookKeys
        :param key: The page, the stage and the step that reads the edit.
        :type key: PageStepKey
        :param mask: The upload.
        :type mask: IncomingFile
        :returns: The SHA-256 digest of the mask and the scratch key it was written to.
        :rtype: tuple[str, StorageKey]
        """
        scratch = keys.page_edit(key.page_id, key.step_id, f'upload-{uuid4().hex}')
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
