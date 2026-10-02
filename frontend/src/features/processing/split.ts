import type { PageEditSchema, PageSchema, RecipeSchema, ScanSchema } from '@/api';

/**
 * What the Split stage knows about the scans of a book: which of them are wider than tall and so look like an open book,
 * which of those are cut already, and which recipe makes the cut.
 *
 * A page is cut from a scan, and its `slot` says how: 0 is the whole scan, 1 and 2 are the left and the right half of a
 * spread. A scan is split when its pages are halves.
 */

/** The processor that decides for each scan, the one that cuts a scan in two, and the one that keeps it whole. */
export const SPLIT_PROCESSOR = {
  auto: 'split.auto',
  spread: 'split.spread',
  whole: 'split.none',
} as const;

/** Whether a scan becomes one page or two. */
export const SplitChoice = { One: 'one', Two: 'two' } as const;

/** One choice of a scan (derived from {@link SplitChoice}). */
export type SplitChoice = (typeof SplitChoice)[keyof typeof SplitChoice];

/** What the banner above the canvas says about the wide scans of the book. */
export interface SplitOffer {
  /** Scans wider than tall. */
  wide: number;
  /** Of those, scans that are split already. */
  split: number;
  /** Of those, scans still whole, which the banner offers to cut. */
  toCut: number;
  /** The pages of the scans still whole, which the run of the cut goes over. */
  pageIds: string[];
}

/** Tell whether a scan is wider than tall, which almost always means two facing pages. */
export function isWide(scan: Pick<ScanSchema, 'facts'>): boolean {
  return scan.facts.width_px > scan.facts.height_px;
}

/** Collect the identifiers of the scans that are wider than tall. */
export function wideScanIds(scans: readonly ScanSchema[]): Set<string> {
  return new Set(scans.filter(isWide).map((scan) => scan.id));
}

/** Give the pages cut from a scan, in the order of their slots. */
export function pagesOfScan(pages: readonly PageSchema[], scanId: string): PageSchema[] {
  return pages.filter((page) => page.scan_id === scanId).toSorted((a, b) => a.slot - b.slot);
}

/** Tell whether the pages of a scan are halves of a spread. */
export function isSplit(scanPages: readonly Pick<PageSchema, 'slot'>[]): boolean {
  return scanPages.some((page) => page.slot > 0);
}

/** Give what a scan is now: one page or two. */
export function choiceOf(scanPages: readonly Pick<PageSchema, 'slot'>[]): SplitChoice {
  return isSplit(scanPages) ? SplitChoice.Two : SplitChoice.One;
}

/**
 * Work out what to offer for the wide scans of the book.
 *
 * @param pages The pages of the book.
 * @param wide The identifiers of the scans that are wider than tall.
 */
export function offerFor(pages: readonly PageSchema[], wide: ReadonlySet<string>): SplitOffer {
  const byScan = new Map<string, PageSchema[]>();
  for (const page of pages) {
    if (page.scan_id !== null && wide.has(page.scan_id)) {
      byScan.set(page.scan_id, [...(byScan.get(page.scan_id) ?? []), page]);
    }
  }
  const whole = [...byScan.values()].filter((scanPages) => !isSplit(scanPages));
  return {
    wide: wide.size,
    split: byScan.size - whole.length,
    toCut: whole.length,
    pageIds: whole.flat().map((page) => page.id),
  };
}

/**
 * Find the recipe of the stage that makes a split of one kind: the one whose first step is that processor.
 *
 * @param recipes The recipes of the Split stage, the active one and the variants.
 * @param choice Whether the scan becomes one page or two.
 */
export function recipeFor(
  recipes: readonly RecipeSchema[],
  choice: SplitChoice,
): RecipeSchema | undefined {
  const key = choice === SplitChoice.Two ? SPLIT_PROCESSOR.spread : SPLIT_PROCESSOR.whole;
  return recipes.find((recipe) => recipe.steps[0]?.processor_key === key);
}

/**
 * Tell whether running a recipe over some pages would undo a split, which deletes the right half of a spread.
 *
 * @param recipe The recipe to run.
 * @param pages The pages the run goes over.
 */
export function undoesSplit(
  recipe: Pick<RecipeSchema, 'steps'>,
  pages: readonly Pick<PageSchema, 'slot'>[],
): boolean {
  return (
    recipe.steps[0]?.processor_key === SPLIT_PROCESSOR.whole && pages.some((page) => page.slot > 0)
  );
}

/** The recipe whose first step is the automatic split, which reads the choice a reader made for a scan. */
export function autoRecipeOf(recipes: readonly RecipeSchema[]): RecipeSchema | undefined {
  return recipes.find((recipe) => recipe.steps[0]?.processor_key === SPLIT_PROCESSOR.auto);
}

/**
 * Find the recipe that cuts a scan in two: the automatic one when the stage has it, since it keeps the choices readers
 * made, and else the one that cuts every scan it is given.
 */
export function cutterOf(recipes: readonly RecipeSchema[]): RecipeSchema | undefined {
  return autoRecipeOf(recipes) ?? recipeFor(recipes, SplitChoice.Two);
}

/** The number of pages a reader chose for a scan, as the edit stores it. */
const PAGES_OF = { [SplitChoice.One]: 1, [SplitChoice.Two]: 2 } as const;

/** Write the form of the edit that stores the choice of a reader: the editor that draws it and its shape as JSON. */
export function choiceForm(choice: SplitChoice): { kind: 'split'; geometry: string } {
  return { kind: 'split', geometry: JSON.stringify({ pages: PAGES_OF[choice] }) };
}

/**
 * Read the choice a reader made for a scan from the edits of its page.
 *
 * @param edits The edits of the Split stage on the page.
 * @returns The choice, or null when the reader left the scan to the automatic split.
 */
export function chosenIn(
  edits: readonly Pick<PageEditSchema, 'processor_key' | 'geometry'>[],
): SplitChoice | null {
  const edit = edits.find((candidate) => candidate.processor_key === SPLIT_PROCESSOR.auto);
  const pages = edit?.geometry?.pages;
  if (pages === PAGES_OF[SplitChoice.One]) {
    return SplitChoice.One;
  }
  return pages === PAGES_OF[SplitChoice.Two] ? SplitChoice.Two : null;
}
