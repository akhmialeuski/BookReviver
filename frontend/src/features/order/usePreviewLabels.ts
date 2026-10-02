import { keepPreviousData, type UseQueryResult, useQuery } from '@tanstack/react-query';
import type { PageSchema } from '@/api';
import { previewPageNumbersApiV1ProjectsProjectIdPagesLabelsPreviewPost } from '@/api';
import { type NumberingDraft, numberingBody, pagesInRange } from '@/features/order/numbering';

/**
 * The labels a numbering would write, asked from the server as the form changes.
 *
 * The server works the labels out by the same rule that saves them, so what the grid shows is what "Apply numbers"
 * stores. The answer is a map from page to its new label, and the previous answer stays on screen while the next
 * is on its way, so the numbers do not blink as the reader types. The pages that moved or changed since the answer
 * was asked make it stale, which `pagesStamp` tells: it is the time the manifest was last read.
 */

/**
 * Ask for the preview of a numbering.
 *
 * @param projectId The book.
 * @param pages The pages of the book in book order.
 * @param draft What the numbering panel holds, or null while the panel is closed.
 * @param pagesStamp When the manifest was last read, which makes the preview be asked again after the pages change.
 * @returns The new label of every page the numbering counts, by page id.
 */
export function usePreviewLabels(
  projectId: string,
  pages: readonly PageSchema[],
  draft: NumberingDraft | null,
  pagesStamp: number,
): UseQueryResult<Map<string, string>> {
  // A range that runs backwards or leaves the book is refused by the server, so it is not asked
  const body = draft === null || pagesInRange(pages, draft) === null ? null : numberingBody(draft);
  return useQuery({
    queryKey: ['order', 'numbering-preview', projectId, body, pagesStamp],
    queryFn: async ({ signal }) => {
      if (body === null) {
        return new Map<string, string>();
      }
      const { data } = await previewPageNumbersApiV1ProjectsProjectIdPagesLabelsPreviewPost({
        path: { project_id: projectId },
        body,
        signal,
        throwOnError: true,
      });
      return new Map(data.map((entry) => [entry.page_id, entry.label]));
    },
    enabled: body !== null,
    placeholderData: keepPreviousData,
  });
}
