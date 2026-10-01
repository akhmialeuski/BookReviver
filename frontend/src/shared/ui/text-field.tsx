import { useId } from 'react';
import { Input } from '@/shared/ui/input';
import { Label } from '@/shared/ui/label';

/**
 * A labelled text input: the label is tied to the input so a click on it focuses the field and screen readers
 * announce it. Every form of the interface builds its fields from this one component.
 */

export function TextField({
  label,
  hint,
  ...props
}: { label: string; hint?: string } & React.ComponentProps<'input'>): React.JSX.Element {
  const id = useId();
  const hintId = `${id}-hint`;
  return (
    <div className="grid gap-2">
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} aria-describedby={hint === undefined ? undefined : hintId} {...props} />
      {hint === undefined ? null : (
        <p id={hintId} className="text-xs text-muted-foreground">
          {hint}
        </p>
      )}
    </div>
  );
}
