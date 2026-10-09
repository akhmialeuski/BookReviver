import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import type { ProjectSchema } from '@/api';
import { deleteProjectApiV1ProjectsProjectIdDeleteMutation } from '@/api/@tanstack/react-query.gen';
import { invalidateProjectList } from '@/features/projects/queries';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { ConfirmDialog } from '@/shared/ui/confirm-dialog';

/**
 * A dialog that asks before it removes a book with all its files, scans and pages, which cannot be undone.
 *
 * The button that opens it is the child, so the screen decides how deleting is offered.
 */

export function DeleteProjectDialog({
  project,
  children,
  onDeleted,
}: {
  project: ProjectSchema;
  /** The button that opens the dialog. */
  children: React.ReactNode;
  /** Called once the book is gone, for a screen that shows it and must leave. */
  onDeleted?: () => void;
}): React.JSX.Element {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const remove = useMutation({
    ...deleteProjectApiV1ProjectsProjectIdDeleteMutation(),
    onSuccess: async () => {
      await invalidateProjectList(queryClient);
      setOpen(false);
      onDeleted?.();
    },
  });
  const title = project.details.title || MESSAGES.projects.untitled;

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={setOpen}
      trigger={children}
      title={MESSAGES.projects.delete.title}
      description={MESSAGES.projects.delete.description(title)}
      error={remove.isError ? describeError(remove.error) : null}
      submitLabel={
        remove.isPending ? MESSAGES.projects.delete.submitting : MESSAGES.projects.delete.submit
      }
      submitDisabled={remove.isPending}
      onConfirm={() => remove.mutate({ path: { project_id: project.id } })}
    />
  );
}
