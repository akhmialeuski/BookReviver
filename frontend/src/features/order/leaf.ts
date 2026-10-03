import type { BlankFill, PageKind, PageSchema } from '@/api';

/**
 * Which pages may show a leaf in place of their scan, and what the reader is told when a change of kind takes a leaf
 * away. The rules are the server's: a leaf stands in place of the scan of a blank page cut from a scan, and a page
 * that stops being blank shows its scan again.
 */

/** The choices for the image of a blank page, in the order the panel lists them. */
export const BLANK_FILLS: readonly BlankFill[] = ['scan', 'white', 'paper'];

/**
 * Pick the pages whose image the reader may choose: blank pages that were cut from a scan.
 *
 * @param pages Pages of the book.
 * @returns The pages that may show a leaf, in the order given.
 */
export function leafPages(pages: readonly PageSchema[]): PageSchema[] {
  return pages.filter((page) => page.kind === 'blank' && page.origin === 'scan');
}

/**
 * Count the pages that show a leaf which a change of kind would replace with the scan.
 *
 * @param pages The pages whose kind is being changed.
 * @param kind The kind they are given.
 * @returns How many of them show a leaf now and will not stand as blank pages after the change.
 */
export function leavesLost(pages: readonly PageSchema[], kind: PageKind): number {
  return kind === 'blank' ? 0 : pages.filter((page) => page.blank_fill !== 'scan').length;
}
