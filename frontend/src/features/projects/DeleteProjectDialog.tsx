import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Trash2Icon } from 'lucide-react';
import { useState } from 'react';
import type { ProjectSchema } from '@/api';
import { deleteProjectApiV1ProjectsProjectIdDeleteMutation } from '@/api/@tanstack/react-query.gen';
import { invalidateProjectList } from '@/features/projects/queries';
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
 * A delete button that asks before it removes a book with all its files, scans and pages, which cannot be undone.
 */

export function DeleteProjectDialog({ project }: { project: ProjectSchema }): React.JSX.Element {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const remove = useMutation({
    ...deleteProjectApiV1ProjectsProjectIdDeleteMutation(),
    onSuccess: async () => {
      await invalidateProjectList(queryClient);
      setOpen(false);
    },
  });
  const title = project.details.title || MESSAGES.projects.untitled;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={`${MESSAGES.projects.delete.open} ${title}`}
        >
          <Trash2Icon />
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{MESSAGES.projects.delete.title}</DialogTitle>
          <DialogDescription>{MESSAGES.projects.delete.description(title)}</DialogDescription>
        </DialogHeader>
        {remove.isError ? <ErrorAlert message={describeError(remove.error)} /> : null}
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">{MESSAGES.common.cancel}</Button>
          </DialogClose>
          <Button
            variant="destructive"
            disabled={remove.isPending}
            onClick={() => remove.mutate({ path: { project_id: project.id } })}
          >
            {remove.isPending
              ? MESSAGES.projects.delete.submitting
              : MESSAGES.projects.delete.submit}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
