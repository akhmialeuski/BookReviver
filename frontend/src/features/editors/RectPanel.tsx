import { HintPanel } from '@/features/editors/HintPanel';
import { MESSAGES } from '@/shared/messages';

/** The part of the frame editor in the panel: the sentence that says how a handle is moved. */
export function RectPanel(): React.JSX.Element {
  return <HintPanel hint={MESSAGES.editors.rect.hint} testId="rect-hint" />;
}
