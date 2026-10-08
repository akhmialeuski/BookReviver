import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/shared/ui/dialog';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * A dialog that asks before something is removed: the question, the reason the last try failed when it did, "Cancel",
 * and a destructive button that carries the removal out.
 *
 * The screen keeps the open state and the request, so it decides when the dialog closes: on the answer of the server, and
 * not on the click.
 */

export function ConfirmDialog({
  open,
  onOpenChange,
  trigger,
  title,
  description,
  error,
  submitLabel,
  submitDisabled = false,
  submitTestId,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The button that opens the dialog, or absent when the screen opens it. */
  trigger?: React.ReactNode;
  title: string;
  description: string;
  /** The sentence that says why the last try failed, or null when it did not. */
  error: string | null;
  /** The words on the destructive button, which say what is being done while the request is on its way. */
  submitLabel: string;
  submitDisabled?: boolean;
  submitTestId?: string;
  onConfirm: () => void;
}): React.JSX.Element {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {trigger === undefined ? null : <DialogTrigger asChild>{trigger}</DialogTrigger>}
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        {error === null ? null : <ErrorAlert message={error} />}
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">{MESSAGES.common.cancel}</Button>
          </DialogClose>
          <Button
            variant="destructive"
            disabled={submitDisabled}
            data-testid={submitTestId}
            onClick={onConfirm}
          >
            {submitLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
