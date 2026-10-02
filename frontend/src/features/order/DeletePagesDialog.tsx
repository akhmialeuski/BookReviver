import { useDeletePages } from '@/features/pages/actions';
import { describePageError } from '@/features/pages/errors';
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
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * Asks to confirm the deletion of the selected pages, which removes them with their images and versions and leaves
 * their scans and files, so the pages can be cut again.
 */

export function DeletePagesDialog({
  projectId,
  pageIds,
  onClose,
}: {
  projectId: string;
  /** The pages to delete, or null while the dialog is closed. */
  pageIds: readonly string[] | null;
  onClose: () => void;
}): React.JSX.Element {
  const remove = useDeletePages(projectId);
  const count = pageIds?.length ?? 0;
  return (
    <Dialog
      open={pageIds !== null}
      onOpenChange={(open) => {
        if (!open) {
          remove.reset();
          onClose();
        }
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{MESSAGES.order.remove.title(count)}</DialogTitle>
          <DialogDescription>{MESSAGES.order.remove.description}</DialogDescription>
        </DialogHeader>
        {remove.isError ? <ErrorAlert message={describePageError(remove.error)} /> : null}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            {MESSAGES.common.cancel}
          </Button>
          <Button
            variant="destructive"
            disabled={remove.isPending || pageIds === null}
            onClick={() => {
              if (pageIds !== null) {
                remove.mutate(pageIds, { onSuccess: onClose });
              }
            }}
          >
            {remove.isPending
              ? MESSAGES.order.remove.submitting
              : MESSAGES.order.remove.submit(count)}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
