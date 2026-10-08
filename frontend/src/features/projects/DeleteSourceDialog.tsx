import { Trash2Icon } from 'lucide-react';
import { useState } from 'react';
import type { SourceSchema } from '@/api';
import { useDeleteSource } from '@/features/pages/actions';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { ConfirmDialog } from '@/shared/ui/confirm-dialog';

/**
 * A delete button, wide as the panel of the Import stage that holds it, that asks before it removes an uploaded file
 * with its scans.
 *
 * The pages of the book keep their own copies of the images, so they stay; what is lost is the way back to the scan,
 * which the text of the question says. While the book is importing the server refuses with a conflict, and its
 * reason is shown in the dialog.
 */

export function DeleteSourceDialog({
  projectId,
  source,
}: {
  projectId: string;
  source: SourceSchema;
}): React.JSX.Element {
  const [open, setOpen] = useState(false);
  const remove = useDeleteSource(projectId);

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        remove.reset();
      }}
      trigger={
        <Button
          variant="outline"
          className="w-full border-destructive/40 text-destructive hover:text-destructive"
        >
          <Trash2Icon />
          {MESSAGES.book.sources.remove}
        </Button>
      }
      title={MESSAGES.book.sources.removeTitle}
      description={MESSAGES.book.sources.removeDescription(source.file_name, source.scan_count)}
      error={remove.isError ? describeError(remove.error) : null}
      submitLabel={
        remove.isPending ? MESSAGES.book.sources.removing : MESSAGES.book.sources.removeSubmit
      }
      submitDisabled={remove.isPending}
      onConfirm={() =>
        remove.mutate(
          { path: { project_id: projectId, source_id: source.id } },
          { onSuccess: () => setOpen(false) },
        )
      }
    />
  );
}
