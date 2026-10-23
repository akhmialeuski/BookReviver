import type { QueryClient } from '@tanstack/react-query';
import type { Stage } from '@/api';
import {
  bookApiV1BooksBookIdGetOptions,
  listStagesApiV1BooksBookIdStagesGetOptions,
} from '@/api/@tanstack/react-query.gen';
import { manifestOptions } from '@/features/pages/manifest';
import { placeOptions } from '@/features/place/queries';
import { type ResumeTarget, resumeTarget } from '@/features/place/resume';
import { STAGES } from '@/features/stages/stages';

/**
 * Finds the screen a book opens on, from the place the server keeps and from what the book holds now.
 *
 * The place is read afresh each time, since another device may have moved on. Whatever cannot be read leaves the
 * place as it is, so a failed request never sends the reader away from where they were: the book opens on the stage
 * and the page the place names, and the screen asks for the book again and shows the error if it persists.
 */

/** Run a request whose failure is the same as an answer that tells nothing. */
async function attempt<ResultT>(request: () => Promise<ResultT>): Promise<ResultT | null> {
  try {
    return await request();
  } catch {
    return null;
  }
}

/**
 * Make sure the place of a book is in the cache before a screen of the book opens.
 *
 * A screen reached by a link or a reload, and not through the address of the book, finds the place here: it needs the
 * stage the reader came from in the reading mode, and the position of the canvas to return to. A place that cannot be
 * read is the same as none.
 *
 * @param queryClient The client whose cache the screens read.
 * @param projectId The book.
 */
export async function loadPlace(queryClient: QueryClient, bookId: string): Promise<void> {
  await attempt(() => queryClient.ensureQueryData(placeOptions(bookId)));
}

/**
 * Find the screen a book opens on.
 *
 * @param queryClient The client whose cache the screens read, which the requests fill.
 * @param projectId The book.
 */
export async function openBook(queryClient: QueryClient, bookId: string): Promise<ResumeTarget> {
  const path = { book_id: bookId };
  const [place, book] = await Promise.all([
    attempt(() => queryClient.fetchQuery({ ...placeOptions(bookId), staleTime: 0 })),
    attempt(() => queryClient.fetchQuery(bookApiV1BooksBookIdGetOptions({ path }))),
  ]);
  const [pages, stages] = await Promise.all([
    place === null || place.page_id === null
      ? null
      : attempt(() => queryClient.fetchQuery(manifestOptions(bookId))),
    place?.mode === 'workspace'
      ? attempt(() => queryClient.fetchQuery(listStagesApiV1BooksBookIdStagesGetOptions({ path })))
      : null,
  ]);
  const everyStage: readonly Stage[] = STAGES.map((entry) => entry.stage);
  return resumeTarget(place, {
    nextStage: book?.next_stage ?? null,
    // A list that could not be read says nothing against the place, so the page and the stage it names stay
    pageIds: new Set(pages === null ? [place?.page_id ?? ''] : pages.map((page) => page.id)),
    availableStages: new Set(
      stages === null
        ? everyStage
        : stages.items.filter((entry) => entry.available).map((entry) => entry.stage),
    ),
  });
}
