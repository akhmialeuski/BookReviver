"""What a page keeps for one step of a recipe: its own settings, its manual edit and their carry-over."""

import hashlib
import json
from typing import TYPE_CHECKING, Any, Self

from attrs import evolve, field, frozen, validators

from bookreviver.domain.errors import ConflictError
from bookreviver.domain.geometry import EditorKind, geometry_from_data
from bookreviver.domain.ids import StorageKey
from bookreviver.domain.labels import LabeledStrEnum
from bookreviver.domain.versions import VERSION_ID_LENGTH

if TYPE_CHECKING:
    from datetime import datetime

    from bookreviver.domain.geometry import EditGeometry
    from bookreviver.domain.ids import PageId, StepId
    from bookreviver.domain.metadata import MetadataMap
    from bookreviver.domain.stages import Stage


class StepLayer(LabeledStrEnum):
    """One of the three layers a page keeps for a step, which a change of the history names."""

    SETTINGS = 'settings', 'Settings of the page'
    FOUND = 'found', 'Found by the automatic run'
    HAND = 'hand', 'Set by hand'


class CarryScope(LabeledStrEnum):
    """The pages a setting of one page is carried over to."""

    FOLLOWING = 'following', 'The following pages'
    SELECTED = 'selected', 'The selected pages'
    KIND = 'kind', 'All pages of the kind of the step'


@frozen
class PageStepKey:
    """The key of a step of a recipe on a page, under which the page keeps its settings and its manual edit of the step.

    :ivar page_id: Page the state belongs to.
    :ivar stage: Stage of the step.
    :ivar step_id: The step, which tells two steps of one processor apart.
    """

    page_id: PageId
    stage: Stage
    step_id: StepId


@frozen(kw_only=True)
class CarryRequest:
    """What a carry-over asks for: the shape a page has set by hand for a step, and the pages it goes to.

    :ivar key: The source page, the stage and the step.
    :ivar scope: The pages the shape goes to.
    :ivar page_ids: The pages of a carry-over to the selected pages, which the other scopes ignore.
    :ivar overwrite: Whether a page that has a shape of its own takes the shape as well, instead of being skipped.
    """

    key: PageStepKey
    scope: CarryScope
    page_ids: tuple[PageId, ...] = ()
    overwrite: bool = False


@frozen(kw_only=True)
class NewPageEdit:
    """A manual edit as the user sends it, before it is stored with its hash.

    :ivar kind: Editor that made the edit.
    :ivar geometry: The shape the user drew, or None for an edit that is only a mask.
    """

    kind: EditorKind
    geometry: EditGeometry | None = None

    def __attrs_post_init__(self) -> None:
        """Check that the shape is the one the editor draws.

        :raises ValueError: If the shape belongs to another editor.
        """
        if self.geometry is not None and self.geometry.editor is not self.kind:
            err_msg = f'The {self.kind.label.lower()} editor does not draw a {self.geometry.editor.label.lower()}.'
            raise ValueError(err_msg)


LAYER_NOT_KEPT: str = 'The layer {layer} of a step is not kept on the page yet.'


# The names of the fields of a manual edit as the history keeps it and of its shape as the hash of the edit reads it
KIND_FIELD: str = 'kind'
GEOMETRY_FIELD: str = 'geometry'
MASK_KEY_FIELD: str = 'mask_key'
EDIT_HASH_FIELD: str = 'edit_hash'


@frozen(kw_only=True)
class PageEdit:
    """A manual edit of a page that a processor reads as an input beside its parameters.

    A frame, an angle, a split line or the mask of an eraser is stored apart from the parameters of the step. The hash
    of the edit joins the identifier of the versions that read it, so a changed edit gives a new version and the old
    edit finds the old version again.

    :ivar page_id: Page the edit belongs to.
    :ivar stage: Stage of the step reading the edit.
    :ivar step_id: The step of a recipe reading the edit, so two steps of one processor keep their own edits.
    :ivar kind: Editor that made the edit.
    :ivar geometry: The shape the user drew, or None for an edit that is only a mask.
    :ivar mask_key: Storage key of the mask the user painted, or None for an edit without one.
    :ivar edit_hash: Hash of the geometry and of the mask, which joins the identifiers of the versions that read it.
    :ivar updated_at: When the edit was last saved.
    """

    page_id: PageId
    stage: Stage
    step_id: StepId
    kind: EditorKind
    geometry: EditGeometry | None = None
    mask_key: StorageKey | None = None
    edit_hash: str = field(validator=validators.min_len(1))
    updated_at: datetime

    @property
    def key(self) -> PageStepKey:
        """The key the edit is stored under."""
        return PageStepKey(self.page_id, self.stage, self.step_id)

    def to_snapshot(self) -> dict[str, Any]:
        """Return the edit as the JSON object the history keeps for the manual layer of a step.

        The object holds what the edit is and not when it was saved, so two saves of the same shape give equal
        snapshots. Its values are JSON types only, which is what a stored snapshot reads back as.

        :returns: The editor, the shape, the key of the mask and the hash of the edit.
        :rtype: dict[str, Any]
        """
        shape = None if self.geometry is None else self.geometry.to_data()
        return json.loads(
            json.dumps(
                {
                    KIND_FIELD: self.kind.value,
                    GEOMETRY_FIELD: shape,
                    MASK_KEY_FIELD: self.mask_key,
                    EDIT_HASH_FIELD: self.edit_hash,
                }
            )
        )

    @classmethod
    def from_snapshot(cls, key: PageStepKey, data: MetadataMap, updated_at: datetime) -> Self:
        """Rebuild the edit a snapshot of the history holds, which is how an undo puts back an edit.

        :param key: The page, the stage and the step the edit belongs to.
        :type key: PageStepKey
        :param data: The snapshot ``to_snapshot`` made.
        :type data: MetadataMap
        :param updated_at: When the edit is put back.
        :type updated_at: datetime
        :returns: The edit, with the hash and the mask key it had.
        :rtype: Self
        """
        kind = EditorKind(data[KIND_FIELD])
        shape = data[GEOMETRY_FIELD]
        mask_key = data[MASK_KEY_FIELD]
        return cls(
            page_id=key.page_id,
            stage=key.stage,
            step_id=key.step_id,
            kind=kind,
            geometry=None if shape is None else geometry_from_data(kind, shape),
            mask_key=None if mask_key is None else StorageKey(mask_key),
            edit_hash=data[EDIT_HASH_FIELD],
            updated_at=updated_at,
        )

    @staticmethod
    def hash_of(geometry: EditGeometry | None, mask_sha256: str | None) -> str:
        """Return the hash of an edit, from its shape in canonical JSON and the SHA-256 digest of its mask.

        :param geometry: The shape the user drew, or None.
        :type geometry: EditGeometry | None
        :param mask_sha256: SHA-256 digest of the mask file, or None for an edit without one.
        :type mask_sha256: str | None
        :returns: The hash cut to 16 lower-case hexadecimal digits, as long as a version identifier.
        :rtype: str
        """
        shape = None if geometry is None else {KIND_FIELD: geometry.editor.value, **geometry.to_data()}
        text = json.dumps([shape, mask_sha256], sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(text.encode()).hexdigest()[:VERSION_ID_LENGTH]


@frozen(kw_only=True)
class PageStepState:
    """What a page keeps for one step of a recipe: its own settings of the step and the manual edit the step reads.

    The settings are only the fields of the parameters of the step that the user changed for this page. A run takes
    every other field from the odd or the even pages, from the group of the page, or from the step of the recipe, in
    that order of weakness, so a field changed in one of them reaches each page that did not change it itself. The
    values join the parameters the step runs with, which the identifier of a version hashes, so a page whose effective
    parameters equal those of another finds the same version in the cache.

    :ivar page_id: Page the state belongs to.
    :ivar stage: Stage of the step.
    :ivar step_id: The step of a recipe, so two steps of one processor keep their own state.
    :ivar params: The fields of the parameters of the step that this page changes, by name.
    :ivar edit: The manual edit the step reads on this page, or None.
    :ivar updated_at: When the state was last saved.
    """

    page_id: PageId
    stage: Stage
    step_id: StepId
    params: MetadataMap = field(factory=dict)
    edit: PageEdit | None = None
    updated_at: datetime

    @property
    def key(self) -> PageStepKey:
        """The key the state is stored under."""
        return PageStepKey(self.page_id, self.stage, self.step_id)

    @property
    def is_empty(self) -> bool:
        """Whether the state holds neither a setting nor an edit, so there is nothing left to store."""
        return not self.params and self.edit is None

    def layer(self, layer: StepLayer) -> dict[str, Any] | None:
        """Return the content of one layer as the history keeps it.

        :param layer: The layer.
        :type layer: StepLayer
        :returns: The fields the page changes, or the snapshot of the manual edit, or None for an empty layer.
        :rtype: dict[str, Any] | None
        :raises ConflictError: For the layer of what the automatic run found, which no state keeps yet.
        """
        match layer:
            case StepLayer.SETTINGS:
                return dict(self.params) or None
            case StepLayer.HAND:
                return None if self.edit is None else self.edit.to_snapshot()
            case StepLayer.FOUND:
                raise ConflictError(LAYER_NOT_KEPT.format(layer=layer.label))

    def with_layer(self, layer: StepLayer, content: MetadataMap | None, updated_at: datetime) -> Self:
        """Return the state with one layer set to a content the history kept, which is how an undo writes it back.

        :param layer: The layer.
        :type layer: StepLayer
        :param content: What ``layer`` returned, or None to empty the layer.
        :type content: MetadataMap | None
        :param updated_at: When the layer is set.
        :type updated_at: datetime
        :returns: The state with the layer set.
        :rtype: Self
        :raises ConflictError: For the layer of what the automatic run found, which no state keeps yet.
        """
        match layer:
            case StepLayer.SETTINGS:
                return evolve(self, params=dict(content or {}), updated_at=updated_at)
            case StepLayer.HAND:
                edit = None if content is None else PageEdit.from_snapshot(self.key, content, updated_at)
                return evolve(self, edit=edit, updated_at=updated_at)
            case StepLayer.FOUND:
                raise ConflictError(LAYER_NOT_KEPT.format(layer=layer.label))
