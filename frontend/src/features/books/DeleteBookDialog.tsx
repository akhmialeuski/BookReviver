import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import type { BookSchema } from '@/api';
import { deleteBookApiV1BooksBookIdDeleteMutation } from '@/api/@tanstack/react-query.gen';
import { invalidateBookList } from '@/features/books/queries';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { ConfirmDialog } from '@/shared/ui/confirm-dialog';

/**
 * A dialog that asks before it removes a book with all its files, scans and pages, which cannot be undone.
 *
 * The button that opens it is the child, so the screen decides how deleting is offered.
 */

export function DeleteBookDialog({
  book,
  children,
  onDeleted,
}: {
  book: BookSchema;
  /** The button that opens the dialog. */
  children: React.ReactNode;
  /** Called once the book is gone, for a screen that shows it and must leave. */
  onDeleted?: () => void;
}): React.JSX.Element {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const remove = useMutation({
    ...deleteBookApiV1BooksBookIdDeleteMutation(),
    onSuccess: async () => {
      await invalidateBookList(queryClient);
      setOpen(false);
      onDeleted?.();
    },
  });
  const title = book.details.title || MESSAGES.books.untitled;

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={setOpen}
      trigger={children}
      title={MESSAGES.books.delete.title}
      description={MESSAGES.books.delete.description(title)}
      error={remove.isError ? describeError(remove.error) : null}
      submitLabel={
        remove.isPending ? MESSAGES.books.delete.submitting : MESSAGES.books.delete.submit
      }
      submitDisabled={remove.isPending}
      onConfirm={() => remove.mutate({ path: { book_id: book.id } })}
    />
  );
}
