import { HintPanel } from '@/features/editors/HintPanel';
import { isPlacement } from '@/features/editors/placement';
import type { RectShape } from '@/features/editors/shapes';
import type { PanelProps } from '@/features/editors/types';
import { MESSAGES } from '@/shared/messages';

/**
 * The part of the frame editor in the panel: the sentence that says how a handle is moved, which says another thing for the
 * step that places the block on the page than for the one that cuts it.
 */
export function RectPanel({ processorKey }: PanelProps<RectShape>): React.JSX.Element {
  return (
    <HintPanel
      hint={
        isPlacement(processorKey)
          ? MESSAGES.editors.rect.placement.hint
          : MESSAGES.editors.rect.hint
      }
      testId="rect-hint"
    />
  );
}
