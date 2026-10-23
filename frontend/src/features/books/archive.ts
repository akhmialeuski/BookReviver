import { client } from '@/api/client.gen';

/**
 * Where the archive of a book is downloaded from.
 *
 * The browser downloads the archive through a plain link, since a book can weigh gigabytes and the server streams it
 * as it is written, which a request of the page would hold in memory whole.
 */

const ARCHIVE_URL = '/api/v1/books/{book_id}/archive';

/** The address of the archive of a book, which the server names after the title of the book. */
export function archiveUrl(bookId: string): string {
  return client.buildUrl({ url: ARCHIVE_URL, path: { book_id: bookId } });
}
