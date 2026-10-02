import { HandIcon } from 'lucide-react';

/** The part of an editor in the panel that only says how the shape is moved and when it is saved. */
export function HintPanel({ hint, testId }: { hint: string; testId: string }): React.JSX.Element {
  return (
    <p
      className="flex basis-full items-start gap-2 rounded-lg border bg-muted/40 p-3 text-sm"
      data-testid={testId}
    >
      <HandIcon className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      {hint}
    </p>
  );
}
