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
 * server refuses it without one. The dialog only asks; the caller sends the run when it is confirmed. It asks in one of
 * two ways: the reader chose one page, or the reader returned the scan to the automatic split, which may keep it whole.
 */

/** What the reader asked for that may delete the right page of a spread. */
export const UnsplitQuestion = { One: 'one', Auto: 'auto' } as const;

/** One question (derived from {@link UnsplitQuestion}). */
export type UnsplitQuestion = (typeof UnsplitQuestion)[keyof typeof UnsplitQuestion];

export function UnsplitDialog({
  question,
  onConfirm,
  onCancel,
}: {
  question: UnsplitQuestion | null;
  onConfirm: () => void;
  onCancel: () => void;
}): React.JSX.Element {
  const labels = MESSAGES.processing.split;
  const words = question === UnsplitQuestion.Auto ? labels.confirmAuto : labels.confirmOne;
  return (
    <Dialog open={question !== null} onOpenChange={(next) => !next && onCancel()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{words.title}</DialogTitle>
          <DialogDescription>{words.body}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>
            {labels.cancel}
          </Button>
          <Button variant="destructive" data-testid="unsplit-confirm" onClick={onConfirm}>
            {words.confirm}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
