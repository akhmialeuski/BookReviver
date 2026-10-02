import { Trash2Icon } from 'lucide-react';
import { useState } from 'react';
import type { SourceSchema } from '@/api';
import { useDeleteSource } from '@/features/pages/actions';
import { describeError } from '@/shared/http/problem';
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
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        remove.reset();
      }}
    >
      <DialogTrigger asChild>
        <Button
          variant="outline"
          className="w-full border-destructive/40 text-destructive hover:text-destructive"
        >
          <Trash2Icon />
          {MESSAGES.book.sources.remove}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{MESSAGES.book.sources.removeTitle}</DialogTitle>
          <DialogDescription>
            {MESSAGES.book.sources.removeDescription(source.file_name, source.scan_count)}
          </DialogDescription>
        </DialogHeader>
        {remove.isError ? <ErrorAlert message={describeError(remove.error)} /> : null}
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">{MESSAGES.common.cancel}</Button>
          </DialogClose>
          <Button
            variant="destructive"
            disabled={remove.isPending}
            onClick={() =>
              remove.mutate(
                { path: { project_id: projectId, source_id: source.id } },
                { onSuccess: () => setOpen(false) },
              )
            }
          >
            {remove.isPending ? MESSAGES.book.sources.removing : MESSAGES.book.sources.removeSubmit}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
