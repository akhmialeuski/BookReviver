import { useState } from 'react';
import { useCollectionReport, useCollectVersions } from '@/features/processing/queries';
import { describeError } from '@/shared/http/problem';
import { formatBytes } from '@/shared/lib/format';
import { MESSAGES } from '@/shared/messages';
import { ConfirmDialog } from '@/shared/ui/confirm-dialog';

/**
 * A dialog that asks before the old results of a book are deleted with their pictures, and says how many results go
 * and how much room that frees.
 *
 * The count is read from the server each time the dialog opens, so it is what the deletion would do at that moment.
 * The button that opens it is the child. Confirming queues the collection, which the activity of the book follows, and
 * closes the dialog; while a run or another job holds the book the server refuses with a conflict, and its reason is
 * shown in the dialog.
 */

export function ClearOldResults({
  projectId,
  children,
}: {
  projectId: string;
  /** The button that opens the dialog. */
  children: React.ReactNode;
}): React.JSX.Element {
  const labels = MESSAGES.about.clearResults;
  const [open, setOpen] = useState(false);
  const report = useCollectionReport(projectId, open);
  const collect = useCollectVersions(projectId);
  // A count that is being read again is the one of an earlier opening, so it is not shown
  const counted = report.isFetching ? undefined : report.data;
  const description = report.isFetching
    ? labels.counting
    : counted === undefined
      ? labels.unavailable
      : counted.versions === 0
        ? labels.nothing
        : labels.description(counted.versions, formatBytes(counted.size_bytes));
  const failure = report.isError ? report.error : collect.error;

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        collect.reset();
      }}
      trigger={children}
      title={labels.title}
      description={description}
      error={failure === null ? null : describeError(failure)}
      submitLabel={collect.isPending ? labels.submitting : labels.submit}
      submitDisabled={counted === undefined || counted.versions === 0 || collect.isPending}
      submitTestId="clear-old-results-submit"
      onConfirm={() =>
        collect.mutate({ path: { project_id: projectId } }, { onSuccess: () => setOpen(false) })
      }
    />
  );
}
