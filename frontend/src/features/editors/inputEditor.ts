import { type EditorDefinition, Picture } from '@/features/editors/types';
import { sourceSize } from '@/features/processing/results';

/**
 * What the editors that lie on the picture their step reads have in common: they are offered once the step has run on the
 * page, belong to the open page, take their size from the step, and run the stage after every save.
 *
 * An editor spreads it into its definition and overrides what differs, such as the size when the step does not report one.
 */

export const INPUT_EDITOR = {
  picture: Picture.Input,
  alwaysOn: false,
  needsResult: true,
  owner: ({ current }) => current.page,
  size: ({ result, pictureSize }) => sourceSize(result) ?? pictureSize,
  runsAfterEdit: () => true,
} as const satisfies Pick<
  EditorDefinition<unknown>,
  'picture' | 'alwaysOn' | 'needsResult' | 'owner' | 'size' | 'runsAfterEdit'
>;
