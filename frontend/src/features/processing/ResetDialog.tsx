import type { ResetImpactSchema } from '@/api';
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
 * Warns that a reset to the defaults takes the settings and the hand edits of a number of pages, before it is sent.
 *
 * The reset reaches pages other than the open one, so the dialog names the scope, how many pages lose work and how many
 * of them have an edit or a setting, and says the reset is written to the history, so one undo gives it back. The caller
 * sends the reset when it is confirmed, and the server refuses such a reset without the confirmation.
 */

const labels = MESSAGES.processing.reset;

export function ResetDialog({
  impact,
  onConfirm,
  onCancel,
}: {
  /** What the reset would take, or null when nothing is asked. */
  impact: ResetImpactSchema | null;
  onConfirm: () => void;
  onCancel: () => void;
}): React.JSX.Element {
  return (
    <Dialog open={impact !== null} onOpenChange={(next) => !next && onCancel()}>
      <DialogContent data-testid="reset-dialog">
        {impact === null ? null : (
          <>
            <DialogHeader>
              <DialogTitle>{labels.warning.title}</DialogTitle>
              <DialogDescription>
                <span data-testid="reset-pages">
                  {labels.warning.body(
                    labels.scopes[impact.scope],
                    impact.affected,
                    impact.hand_pages,
                    impact.settings_pages,
                  )}
                </span>{' '}
                {labels.warning.undo}
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" onClick={onCancel}>
                {labels.warning.cancel}
              </Button>
              <Button variant="destructive" data-testid="reset-confirm" onClick={onConfirm}>
                {labels.warning.confirm}
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
