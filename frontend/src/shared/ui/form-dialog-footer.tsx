import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { DialogFooter } from '@/shared/ui/dialog';

/**
 * The foot of a form in a dialog that can also remove what it edits: the removal on the left, and "Cancel" and the
 * submit button on the right.
 */

export function FormDialogFooter({
  danger,
  onCancel,
  submitLabel,
  submitDisabled = false,
}: {
  /** The button that removes what the form edits, or absent for a form that makes something new. */
  danger?: React.ReactNode;
  onCancel: () => void;
  submitLabel: string;
  submitDisabled?: boolean;
}): React.JSX.Element {
  return (
    <DialogFooter className="sm:justify-between">
      {danger ?? <span />}
      <div className="flex flex-col-reverse gap-2 sm:flex-row">
        <Button type="button" variant="outline" onClick={onCancel}>
          {MESSAGES.common.cancel}
        </Button>
        <Button type="submit" disabled={submitDisabled}>
          {submitLabel}
        </Button>
      </div>
    </DialogFooter>
  );
}
