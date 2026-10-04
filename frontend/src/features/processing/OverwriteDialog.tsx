import type { RunImpactSchema } from '@/api';
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
 * Warns that a run in a mode that takes work away does so on a number of pages, before the run is sent.
 *
 * The run keeps the settings and the hand edits of the pages unless it is asked to replace the hand settings or reset
 * the page settings, and then it takes them from the pages it goes over. The dialog names how many pages lose work and
 * says the change is written to the history, so one undo gives it back. The caller sends the run when it is confirmed,
 * and the server refuses such a run without the confirmation.
 */

const labels = MESSAGES.processing.modes.warning;

export function OverwriteDialog({
  impact,
  onConfirm,
  onCancel,
}: {
  /** What the run would take and by which mode, or null when nothing is asked. */
  impact: RunImpactSchema | null;
  onConfirm: () => void;
  onCancel: () => void;
}): React.JSX.Element {
  return (
    <Dialog open={impact !== null} onOpenChange={(next) => !next && onCancel()}>
      <DialogContent data-testid="overwrite-dialog">
        {impact === null ? null : (
          <>
            <DialogHeader>
              <DialogTitle>{labels.title(impact.mode)}</DialogTitle>
              <DialogDescription>
                <span data-testid="overwrite-pages">
                  {labels.body(impact.mode, impact.affected)}
                </span>{' '}
                {labels.undo}
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" onClick={onCancel}>
                {labels.cancel}
              </Button>
              <Button variant="destructive" data-testid="overwrite-confirm" onClick={onConfirm}>
                {labels.confirm}
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
