import { useId } from 'react';
import { cn } from '@/shared/lib/utils';
import { Label } from '@/shared/ui/label';

/**
 * A labelled drop-down list over the browser's own `select`, which is accessible and works on a phone without extra
 * code. The options are given as children, and the label is tied to the list like every field of the interface.
 */

export function SelectField({
  label,
  hint,
  className,
  ...props
}: { label: string; hint?: string } & React.ComponentProps<'select'>): React.JSX.Element {
  const id = useId();
  const hintId = `${id}-hint`;
  return (
    <div className="grid gap-2">
      <Label htmlFor={id}>{label}</Label>
      <select
        id={id}
        aria-describedby={hint === undefined ? undefined : hintId}
        className={cn(
          'h-9 w-full min-w-0 rounded-md border border-input bg-background px-3 py-1 text-sm shadow-xs outline-none transition-[color,box-shadow] disabled:cursor-not-allowed disabled:opacity-50',
          'focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50',
          className,
        )}
        {...props}
      />
      {hint === undefined ? null : (
        <p id={hintId} className="text-xs text-muted-foreground">
          {hint}
        </p>
      )}
    </div>
  );
}
