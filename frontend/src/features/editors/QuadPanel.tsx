import { HintPanel } from '@/features/editors/HintPanel';
import { MESSAGES } from '@/shared/messages';

/** The part of the sheet editor in the panel: the sentence that says how a corner is moved. */
export function QuadPanel(): React.JSX.Element {
  return <HintPanel hint={MESSAGES.editors.quad.hint} testId="quad-hint" />;
}
