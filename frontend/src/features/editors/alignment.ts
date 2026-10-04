/**
 * The alignment of the content box on the page of the book, as the Margins step takes it from its settings.
 *
 * A box narrower or lower than the room the margins leave stands at the edge the alignment names. The side margins are told
 * apart by the side of the book, inner and outer, or are the same on every page, left and right, and the horizontal choices
 * follow that setting.
 */

/** The name of the setting that says where a box lower than the room stands. */
export const ALIGN_VERTICAL = 'align_vertical';

/** The name of the setting that says where a box narrower than the room stands. */
export const ALIGN_HORIZONTAL = 'align_horizontal';

/** The name of the setting that says how the side margins are told apart. */
export const MARGINS_BY = 'margins_by';

/** The values of the vertical alignment, as the server writes them. */
export const VERTICAL_CHOICES = ['top', 'center', 'bottom'] as const;

/** One vertical alignment. */
export type VerticalChoice = (typeof VERTICAL_CHOICES)[number];

/** One horizontal alignment. */
export type HorizontalChoice = 'inner' | 'outer' | 'left' | 'right' | 'center';

const INNER_OUTER = ['inner', 'center', 'outer'] as const;
const LEFT_RIGHT = ['left', 'center', 'right'] as const;

/**
 * Give the horizontal alignments the settings of the step offer, from the gutter side to the outer side or from left to
 * right.
 *
 * @param marginsBy The value of the setting `margins_by`.
 */
export function horizontalChoices(marginsBy: unknown): readonly HorizontalChoice[] {
  return marginsBy === 'left-right' ? LEFT_RIGHT : INNER_OUTER;
}

/** Read a setting as one of the choices, or the one the step starts with when it holds none of them. */
export function choiceOf<T extends string>(value: unknown, choices: readonly T[], start: T): T {
  return choices.find((choice) => choice === value) ?? start;
}
