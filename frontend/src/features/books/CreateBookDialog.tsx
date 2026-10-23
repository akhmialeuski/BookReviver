import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { PlusIcon } from 'lucide-react';
import { useState } from 'react';
import { createBookApiV1BooksPostMutation } from '@/api/@tanstack/react-query.gen';
import { invalidateBookList } from '@/features/books/queries';
import { describeError } from '@/shared/http/problem';
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
import { TextField } from '@/shared/ui/text-field';

/**
 * The button that opens the form of a new book. Only the title is required, and a created book opens at once, so
 * the next step, uploading its files, is one click away.
 */

const TITLE_MAX_LENGTH = 500;
const SUBTITLE_MAX_LENGTH = 300;

export function CreateBookDialog(): React.JSX.Element {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState('');
  const [subtitle, setSubtitle] = useState('');

  const create = useMutation({
    ...createBookApiV1BooksPostMutation(),
    onSuccess: async (book) => {
      await invalidateBookList(queryClient);
      setOpen(false);
      setTitle('');
      setSubtitle('');
      await navigate({ to: '/books/$bookId', params: { bookId: book.id } });
    },
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <PlusIcon />
          {MESSAGES.books.create.open}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate({ body: { title, subtitle } });
          }}
        >
          <DialogHeader>
            <DialogTitle>{MESSAGES.books.create.title}</DialogTitle>
            <DialogDescription>{MESSAGES.books.create.description}</DialogDescription>
          </DialogHeader>
          <TextField
            label={MESSAGES.books.create.titleLabel}
            name="title"
            required
            maxLength={TITLE_MAX_LENGTH}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
          <TextField
            label={MESSAGES.books.create.subtitleLabel}
            name="subtitle"
            maxLength={SUBTITLE_MAX_LENGTH}
            value={subtitle}
            onChange={(event) => setSubtitle(event.target.value)}
          />
          {create.isError ? <ErrorAlert message={describeError(create.error)} /> : null}
          <DialogFooter>
            <Button type="submit" disabled={create.isPending || title.trim() === ''}>
              {create.isPending ? MESSAGES.books.create.submitting : MESSAGES.books.create.submit}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
