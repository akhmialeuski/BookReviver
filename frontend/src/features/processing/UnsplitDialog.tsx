import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/ui/dialog';

/**
 * Asks to confirm that a scan goes back to one page, before the run that does it is sent.
 *
 * Going back deletes the right page of the spread together with its work, so the run carries the confirmation and the
 * server refuses it without one. The dialog only asks; the caller sends the run when it is confirmed.
 */

export function UnsplitDialog({
  open,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}): React.JSX.Element {
  const labels = MESSAGES.processing.split;
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onCancel()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{labels.confirmTitle}</DialogTitle>
          <DialogDescription>{labels.confirmBody}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>
            {labels.cancel}
          </Button>
          <Button variant="destructive" data-testid="unsplit-confirm" onClick={onConfirm}>
            {labels.confirm}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
