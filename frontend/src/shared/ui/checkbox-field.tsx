import { useId } from 'react';
import { Label } from '@/shared/ui/label';

/**
 * A native checkbox with its label on the right. The label is a real `label`, so clicking the text toggles it and a
 * screen reader reads them together.
 */

export function CheckboxField({
  label,
  ...props
}: { label: string } & Omit<React.ComponentProps<'input'>, 'type'>): React.JSX.Element {
  const id = useId();
  return (
    <div className="flex items-center gap-2">
      <input id={id} type="checkbox" className="size-4 accent-primary" {...props} />
      <Label htmlFor={id} className="font-normal">
        {label}
      </Label>
    </div>
  );
}
