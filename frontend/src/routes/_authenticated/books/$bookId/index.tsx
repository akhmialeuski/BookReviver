import { createFileRoute, redirect } from '@tanstack/react-router';
import { openBook } from '@/features/place/open';

/**
 * The address of one book, `/projects/<id>`, which shows nothing of its own and sends the reader to where the book was
 * left.
 *
 * The server keeps the place of the account in the book, so the book opens on the same stage, page, layout and filter,
 * or in the reading mode, on any device. A book the account has not worked on opens on the `next_stage` of its summary,
 * and a book with neither opens on the first stage. A page that was deleted or a stage that cannot be worked on is
 * replaced by the nearest view that exists.
 */

export const Route = createFileRoute('/_authenticated/books/$bookId/')({
  beforeLoad: async ({ context, params }) => {
    const target = await openBook(context.queryClient, params.bookId);
    if (target.mode === 'reading') {
      throw redirect({
        to: '/books/$bookId/viewer',
        params: { bookId: params.bookId },
        search: target.search,
        replace: true,
      });
    }
    throw redirect({
      to: '/books/$bookId/stages/$stage',
      params: { bookId: params.bookId, stage: target.stage },
      search: target.search,
      replace: true,
    });
  },
});
