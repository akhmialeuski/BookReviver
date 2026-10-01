import { useId } from 'react';
import { cn } from '@/shared/lib/utils';
import { Label } from '@/shared/ui/label';

/**
 * A labelled multi-line text field, for notes. It matches the single-line input in look and focus ring.
 */

export function TextareaField({
  label,
  className,
  ...props
}: { label: string } & React.ComponentProps<'textarea'>): React.JSX.Element {
  const id = useId();
  return (
    <div className="grid gap-2">
      <Label htmlFor={id}>{label}</Label>
      <textarea
        id={id}
        className={cn(
          'min-h-20 w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs outline-none transition-[color,box-shadow] placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50',
          'focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50',
          className,
        )}
        {...props}
      />
    </div>
  );
}
