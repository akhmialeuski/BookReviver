import type { FigureState } from '@/api';

/**
 * How the shape of a step looks in each of the states it can be in on a page.
 *
 * A shape is on the page from the first time the step is opened, so its look tells what stands behind it: a grey dashed
 * line is the shape the step starts from, which computes nothing and leaves the page as it is, a green line is what the
 * step found, and an orange line is what the reader set, which later runs keep until the reader asks for "Auto".
 */

/** The look of a shape in one state. */
export interface FigureStyle {
  stroke: string;
  /** The dashes of the line, in screen pixels, or undefined for a solid line. */
  dash: number[] | undefined;
}

export const FIGURE_STYLE: Readonly<Record<FigureState, FigureStyle>> = {
  default: { stroke: '#64748b', dash: [6, 4] },
  found: { stroke: '#16a34a', dash: undefined },
  'by-hand': { stroke: '#ea580c', dash: undefined },
  // A page the step passed by has no shape to draw, and the style only keeps the type whole
  skipped: { stroke: '#94a3b8', dash: [2, 4] },
};

/**
 * Tell the state of the shape of a step on the open page.
 *
 * @param edited Whether the reader set the shape, which outranks what the step found.
 * @param made Whether the step has made a version on the page, which is what it found there.
 */
export function figureStateOf(edited: boolean, made: boolean): FigureState {
  if (edited) {
    return 'by-hand';
  }
  return made ? 'found' : 'default';
}
