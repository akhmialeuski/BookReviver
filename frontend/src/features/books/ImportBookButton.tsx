import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { UploadIcon } from 'lucide-react';
import { useRef } from 'react';
import { importBookApiV1BooksArchivePostMutation } from '@/api/@tanstack/react-query.gen';
import { invalidateBookList } from '@/features/books/queries';
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

export function ImportBookButton(): React.JSX.Element {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const input = useRef<HTMLInputElement>(null);

  const importing = useMutation({
    ...importBookApiV1BooksArchivePostMutation(),
    onSuccess: async (book) => {
      await invalidateBookList(queryClient);
      await navigate({ to: '/books/$bookId', params: { bookId: book.id } });
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
        aria-label={MESSAGES.books.import.label}
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
        {importing.isPending ? MESSAGES.books.import.importing : MESSAGES.books.import.open}
      </Button>
      {importing.isError ? <ErrorAlert message={describeError(importing.error)} /> : null}
    </div>
  );
}
