import type { PageSchema, ScanSchema } from '@/api';

/**
 * Facts about a file of the book that the panel of the Import stage states, worked out from the pages of the book and
 * the scans of the file.
 */

/** A run of neighbouring places in the book, counted from 1 as the reader counts them. */
export interface PageRun {
  first: number;
  last: number;
}

/** The lowest and the highest resolution among scans, in dots per inch. */
export interface Resolution {
  min: number;
  max: number;
}

/**
 * Find where the pages cut from a file stand in the book.
 *
 * @param pages Every page of the book, the excluded ones too, as the manifest lists them.
 * @param sourceId The file.
 * @returns The places of its pages as runs of neighbours in book order, or none when no page stands for the file.
 */
export function pageRunsOf(pages: readonly PageSchema[], sourceId: string): PageRun[] {
  const places = pages
    .filter((page) => page.source_id === sourceId)
    .map((page) => page.position + 1)
    .sort((first, second) => first - second);
  const runs: PageRun[] = [];
  for (const place of places) {
    const run = runs.at(-1);
    if (run !== undefined && place === run.last + 1) {
      run.last = place;
    } else {
      runs.push({ first: place, last: place });
    }
  }
  return runs;
}

/** Write runs of places the way a sentence quotes them, such as `1–40, 55`. */
export function formatRuns(runs: readonly PageRun[]): string {
  return runs
    .map((run) => (run.first === run.last ? `${run.first}` : `${run.first}–${run.last}`))
    .join(', ');
}

/**
 * Read the resolution of a file from its scans.
 *
 * @param scans Scans of the file whose images report a resolution; the others are ignored.
 * @returns The lowest and the highest horizontal resolution, or null when no scan reports one.
 */
export function resolutionOf(scans: readonly ScanSchema[]): Resolution | null {
  const values = scans.flatMap((scan) =>
    scan.facts.dpi_x === null ? [] : [Math.round(scan.facts.dpi_x)],
  );
  if (values.length === 0) {
    return null;
  }
  return { min: Math.min(...values), max: Math.max(...values) };
}
