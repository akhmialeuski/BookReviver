import { useDeletePages } from '@/features/pages/actions';
import { describePageError } from '@/features/pages/errors';
import { MESSAGES } from '@/shared/messages';
import { ConfirmDialog } from '@/shared/ui/confirm-dialog';

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
    <ConfirmDialog
      open={pageIds !== null}
      onOpenChange={(open) => {
        if (!open) {
          remove.reset();
          onClose();
        }
      }}
      title={MESSAGES.order.remove.title(count)}
      description={MESSAGES.order.remove.description}
      error={remove.isError ? describePageError(remove.error) : null}
      submitLabel={
        remove.isPending ? MESSAGES.order.remove.submitting : MESSAGES.order.remove.submit(count)
      }
      submitDisabled={remove.isPending || pageIds === null}
      onConfirm={() => {
        if (pageIds !== null) {
          remove.mutate(pageIds, { onSuccess: onClose });
        }
      }}
    />
  );
}
