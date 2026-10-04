import { RotationCanvas } from '@/features/editors/RotationCanvas';
import { RotationPanel } from '@/features/editors/RotationPanel';
import { type RotationShape, readRotation, writeRotation } from '@/features/editors/shapes';
import { type EditorDefinition, Picture } from '@/features/editors/types';

/**
 * The rotation editor: the angle a page is turned by, set with the handles of an axis on the page, the slider and the field
 * in the panel, the wheel or the arrow keys.
 *
 * It lies on the picture the step reads, which is what the angle is turned from. Until the reader sets an angle the editor
 * starts from the one the step found.
 */

export const rotationEditor: EditorDefinition<RotationShape> = {
  picture: Picture.Input,
  alwaysOn: false,
  needsResult: false,
  owner: ({ current }) => current.page,
  size: () => null,
  runsAfterEdit: () => true,
  fallback: ({ result }) => ({ degrees: result?.angle ?? 0 }),
  read: readRotation,
  write: writeRotation,
  Canvas: RotationCanvas,
  Panel: RotationPanel,
};
