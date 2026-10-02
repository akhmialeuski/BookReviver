import type { PageSchema } from '@/api';
import { AttachScanView } from '@/features/pages/AttachScanView';
import { shortName } from '@/features/pages/names';
import { MESSAGES } from '@/shared/messages';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/shared/ui/dialog';

/**
 * The dialog that binds a scan to a missing page, which is the step of the old page dialog moved out on its own: the
 * Order stage offers it from the panel when one missing page is selected.
 */

export function AttachDialog({
  projectId,
  page,
  onClose,
}: {
  projectId: string;
  /** The missing page to bind a scan to, or null while the dialog is closed. */
  page: PageSchema | null;
  onClose: () => void;
}): React.JSX.Element {
  return (
    <Dialog open={page !== null} onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{MESSAGES.pages.attach.title}</DialogTitle>
          <DialogDescription>
            {MESSAGES.pages.attach.description(page === null ? '' : shortName(page))}
          </DialogDescription>
        </DialogHeader>
        {page === null ? null : (
          <AttachScanView projectId={projectId} page={page} onBack={onClose} onDone={onClose} />
        )}
      </DialogContent>
    </Dialog>
  );
}
