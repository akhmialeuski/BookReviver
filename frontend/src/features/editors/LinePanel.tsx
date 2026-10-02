import { HandIcon } from 'lucide-react';
import { MESSAGES } from '@/shared/messages';

/** The part of the split line editor in the panel: the sentence that says how the line is moved and when it is saved. */
export function LinePanel(): React.JSX.Element {
  return (
    <p
      className="flex basis-full items-start gap-2 rounded-lg border bg-muted/40 p-3 text-sm"
      data-testid="line-hint"
    >
      <HandIcon className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      {MESSAGES.editors.line.hint}
    </p>
  );
}
