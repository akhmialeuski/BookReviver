import { useMutation, useQueryClient } from '@tanstack/react-query';
import { UploadIcon } from 'lucide-react';
import { useReducer, useState } from 'react';
import type { JobSchema } from '@/api';
import {
  readJobApiV1JobsJobIdGetQueryKey,
  uploadSourcesApiV1ProjectsProjectIdSourcesPostMutation,
} from '@/api/@tanstack/react-query.gen';
import { DropZone } from '@/features/projects/upload/DropZone';
import { FileReview } from '@/features/projects/upload/FileReview';
import { totalBytes } from '@/features/projects/upload/files';
import { EMPTY_SELECTION, selectionReducer } from '@/features/projects/upload/selection';
import { buildUploadForm } from '@/features/projects/upload/upload-form';
import { describeError } from '@/shared/http/problem';
import { formatBytes } from '@/shared/lib/format';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/shared/ui/dialog';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The dialog that uploads files into a book: choose or drop a folder or files, check the list and its order, send.
 *
 * The request answers as soon as the server has queued the import, with the job. The job is handed to the page,
 * which shows its progress from the event stream, so the dialog closes at once and the list is emptied. If the
 * request fails, for instance because the book already imports another upload, the list stays for another try.
 */

export function UploadDialog({
  projectId,
  onUploaded,
}: {
  projectId: string;
  onUploaded: (job: JobSchema) => void;
}): React.JSX.Element {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [selection, dispatch] = useReducer(selectionReducer, EMPTY_SELECTION);

  const upload = useMutation({
    ...uploadSourcesApiV1ProjectsProjectIdSourcesPostMutation(),
    onSuccess: (job) => {
      // An event of the job can arrive before this answer, and it is newer, so it is not overwritten
      queryClient.setQueryData(
        readJobApiV1JobsJobIdGetQueryKey({ path: { job_id: job.id } }),
        (existing: JobSchema | undefined) => existing ?? job,
      );
      dispatch({ type: 'clear' });
      setOpen(false);
      onUploaded(job);
    },
  });

  const { files } = selection;
  const size = formatBytes(totalBytes(files));

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <UploadIcon />
          {MESSAGES.upload.open}
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{MESSAGES.upload.title}</DialogTitle>
          <DialogDescription>{MESSAGES.upload.description}</DialogDescription>
        </DialogHeader>
        <DropZone onPick={(picked) => dispatch({ type: 'add', picked })} />
        <FileReview selection={selection} dispatch={dispatch} />
        {upload.isError ? <ErrorAlert message={describeError(upload.error)} /> : null}
        <DialogFooter className="items-center">
          {upload.isPending ? (
            <p className="text-sm text-muted-foreground">
              {MESSAGES.upload.sending(files.length, size)}
            </p>
          ) : null}
          <Button
            disabled={files.length === 0 || upload.isPending}
            onClick={() =>
              upload.mutate({
                path: { project_id: projectId },
                body: { files: files.map((entry) => entry.file) },
                // The generated serializer would drop the folders from the names of the parts
                bodySerializer: () => buildUploadForm(files),
              })
            }
          >
            {upload.isPending ? MESSAGES.upload.submitting : MESSAGES.upload.submit(files.length)}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
