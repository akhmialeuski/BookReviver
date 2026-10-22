import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { UploadIcon } from 'lucide-react';
import { useRef } from 'react';
import { importProjectApiV1ProjectsArchivePostMutation } from '@/api/@tanstack/react-query.gen';
import { invalidateProjectList } from '@/features/projects/queries';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The button that takes in the archive of a book, downloaded from this or another instance, as a new book.
 *
 * The chosen file is sent at once, and the imported book opens as a created one does. A file the server refuses
 * leaves the library as it was and says why under the button.
 */

const ARCHIVE_TYPES = '.zip,application/zip';

export function ImportProjectButton(): React.JSX.Element {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const input = useRef<HTMLInputElement>(null);

  const importing = useMutation({
    ...importProjectApiV1ProjectsArchivePostMutation(),
    onSuccess: async (project) => {
      await invalidateProjectList(queryClient);
      await navigate({ to: '/projects/$projectId', params: { projectId: project.id } });
    },
  });

  const choose = (event: React.ChangeEvent<HTMLInputElement>): void => {
    const archive = event.target.files?.[0];
    // The same file can be chosen again after a refusal
    event.target.value = '';
    if (archive !== undefined) {
      importing.mutate({ body: { archive } });
    }
  };

  return (
    <div className="grid justify-items-end gap-2">
      <input
        ref={input}
        type="file"
        accept={ARCHIVE_TYPES}
        aria-label={MESSAGES.projects.import.label}
        className="hidden"
        onChange={choose}
      />
      <Button
        type="button"
        variant="outline"
        disabled={importing.isPending}
        onClick={() => input.current?.click()}
      >
        <UploadIcon />
        {importing.isPending ? MESSAGES.projects.import.importing : MESSAGES.projects.import.open}
      </Button>
      {importing.isError ? <ErrorAlert message={describeError(importing.error)} /> : null}
    </div>
  );
}
