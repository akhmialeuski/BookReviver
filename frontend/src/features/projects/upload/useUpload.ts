import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { JobSchema } from '@/api';
import {
  readJobApiV1JobsJobIdGetQueryKey,
  uploadSourcesApiV1ProjectsProjectIdSourcesPostMutation,
} from '@/api/@tanstack/react-query.gen';
import { invalidateJobs } from '@/features/projects/queries';
import type { UploadFile } from '@/features/projects/upload/files';
import { buildUploadForm } from '@/features/projects/upload/upload-form';

/**
 * The upload of files into a book, shared by the dialog that adds files and by the drop area of an empty book.
 *
 * The request answers as soon as the server has queued the import, with the job. The job is written into its own
 * query unless an event of it got there first, and the jobs of the book are marked stale, so the import shows in the
 * list of files at once and follows the events from there.
 *
 * @param projectId The book.
 * @param onSent Called when the server has accepted the upload.
 * @returns The request, for its pending and failed states, and `send`, which uploads files in the order given.
 */
export function useUpload(projectId: string, onSent?: () => void) {
  const queryClient = useQueryClient();
  const upload = useMutation({
    ...uploadSourcesApiV1ProjectsProjectIdSourcesPostMutation(),
    onSuccess: (job) => {
      // An event of the job can arrive before this answer, and it is newer, so it is not overwritten
      queryClient.setQueryData(
        readJobApiV1JobsJobIdGetQueryKey({ path: { job_id: job.id } }),
        (existing: JobSchema | undefined) => existing ?? job,
      );
      void invalidateJobs(queryClient, projectId);
      onSent?.();
    },
  });

  const send = (files: readonly UploadFile[]): void =>
    upload.mutate({
      path: { project_id: projectId },
      body: { files: files.map((entry) => entry.file) },
      // The generated serializer would drop the folders from the names of the parts
      bodySerializer: () => buildUploadForm(files),
    });

  return { upload, send };
}
