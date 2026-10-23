import { useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { bookEventsApiV1BooksBookIdEventsGet } from '@/api';
import { applyBookEvent, type BookSync, refreshJobs } from '@/features/books/events';
import { invalidateJobReads, invalidateJobs } from '@/features/books/queries';
import { keepListening } from '@/features/books/reconnect';

/**
 * Keeps the open book up to date while its page is shown, by listening to the project's event stream.
 *
 * The stream is read with the generated server-sent events client, which sends the session cookie and gives the
 * name and data of every event. The connection is closed when the page is left. The generated client ends its stream
 * without a word when the server closes it, so reconnecting is done by `keepListening`, which opens the stream again
 * until the page is left. A stream opened again names the version of the book the tab holds as `Last-Event-ID`, and the
 * server answers `book-reset` when the book changed meanwhile and nothing when it did not, so a break costs no read of
 * the book. The jobs raise no version, so the reads that show them are read again after every end. The jobs are read
 * again after every connection too, since the end of a job before the server subscribed the stream has no event.
 */

// The generated client's own retry is off, so that one loop decides when to reconnect
const GENERATED_CLIENT_ATTEMPTS = 1;
// The header that names the version of the book the tab holds when the stream is opened again
const LAST_EVENT_ID = 'Last-Event-ID';

export function useBookEvents(bookId: string): void {
  const queryClient = useQueryClient();

  useEffect(() => {
    const controller = new AbortController();
    const sync: BookSync = { version: null };
    void keepListening({
      signal: controller.signal,
      catchUp: () => {
        void refreshJobs(queryClient, bookId);
        void invalidateJobReads(queryClient);
      },
      open: async (onActivity) => {
        const { stream } = await bookEventsApiV1BooksBookIdEventsGet({
          path: { book_id: bookId },
          headers: sync.version === null ? {} : { [LAST_EVENT_ID]: String(sync.version) },
          signal: controller.signal,
          sseMaxRetryAttempts: GENERATED_CLIENT_ATTEMPTS,
          // The jobs are read again once the server has subscribed this stream, so the end of a job that came between
          // the first read of the screen and the subscription is not lost for good, which would leave the screen
          // waiting for it. The rest of the book is not read again, which costs a long book a heavy second read
          fetch: async (input, init) => {
            const response = await fetch(input, init);
            if (response.ok) {
              void invalidateJobs(queryClient, bookId);
            }
            return response;
          },
          onSseEvent: (event) => {
            onActivity();
            applyBookEvent(queryClient, bookId, event, sync);
          },
        });
        return stream;
      },
    });

    return () => controller.abort();
  }, [bookId, queryClient]);
}
