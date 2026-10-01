import type { LabelStyle, PageKind } from '@/api';
import { MESSAGES } from '@/shared/messages';

/**
 * The closed sets of the page forms, in the order their drop-downs list them, and the check that a value read from a
 * form field is one of them.
 *
 * The lists come from the keys of the message tables, which are declared `satisfies Record<..., string>`, so a kind
 * the API adds stops the build until it has its wording, and shows up in every drop-down without another edit.
 */

export const PAGE_KINDS = Object.keys(MESSAGES.pages.kinds) as PageKind[];

export const LABEL_STYLES = Object.keys(MESSAGES.pages.labelStyles) as LabelStyle[];

/** Narrow the text of a form field to a page kind, or undefined when it is none. */
export function asPageKind(value: string): PageKind | undefined {
  return PAGE_KINDS.find((kind) => kind === value);
}

/** Narrow the text of a form field to a label style, or undefined when it is none. */
export function asLabelStyle(value: string): LabelStyle | undefined {
  return LABEL_STYLES.find((style) => style === value);
}
