import { useMemo } from 'react';
import { countParts, type PageValues, pageName } from '@/features/processing/pageSettings';
import { usePageSettings } from '@/features/processing/queries';
import type { Processing } from '@/features/processing/useProcessing';
import type { StripItem } from '@/features/workspace/strip';

/**
 * Gather what the values of a setting for a part of the pages are told from: the open page, what it and its parts have
 * for the steps, the pages selected in the grid, and how many pages the odd pages, the even pages and each group are.
 *
 * @param processing The stage that is open.
 * @param items Every page of the book with where it stands in the stage.
 * @param current The open page, or undefined when the book has none.
 * @param selected The pages selected in the grid.
 * @returns The values, or undefined when no page is open, since a value for a part of the pages is set from a page.
 */
export function usePageValues(
  processing: Processing,
  items: readonly StripItem[],
  current: StripItem | undefined,
  selected: ReadonlySet<string>,
): PageValues | undefined {
  const { projectId, stage, recipe } = processing;
  const settings = usePageSettings(projectId, current?.page.id, stage);
  const kind = recipe?.kind;
  const parts = useMemo(() => countParts(items, kind), [items, kind]);
  const picked = useMemo(() => [...selected], [selected]);
  return useMemo(
    () =>
      current === undefined
        ? undefined
        : {
            projectId,
            stage,
            page: {
              id: current.page.id,
              name: pageName(current.page.label, current.page.position),
            },
            settings: settings.data ?? [],
            selected: picked,
            ...parts,
          },
    [projectId, stage, current, settings.data, picked, parts],
  );
}
