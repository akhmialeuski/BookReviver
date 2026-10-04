import type { PageSchema, PageUpdate } from '@/api';

/**
 * A change of a page's own fields, worked out in the browser the way the server works it out.
 *
 * The panel of selected pages shows the change before the server has answered, as a move does, so the same rule is
 * applied to the cached manifest at once: a field left out keeps its value, and a label, notes or group sent as null
 * are cleared to the empty text. A page that stops being blank gets its scan back in place of a leaf, as the server
 * does it. A content type sent by the reader is theirs, so the page then says it was set by hand.
 */

/**
 * Apply a change to a page.
 *
 * @param page The page as the manifest has it.
 * @param changes The fields to change, as the body of the update request has them.
 * @returns The page with the change made, or the same page object when the change leaves it as it is.
 */
export function applyChanges(page: PageSchema, changes: PageUpdate): PageSchema {
  const next: PageSchema = {
    ...page,
    label: changes.label === undefined ? page.label : (changes.label ?? ''),
    kind: changes.kind ?? page.kind,
    content_type: changes.content_type ?? page.content_type,
    content_source: changes.content_type === undefined ? page.content_source : 'hand',
    included: changes.included ?? page.included,
    notes: changes.notes === undefined ? page.notes : (changes.notes ?? ''),
    group_label: changes.group_label === undefined ? page.group_label : (changes.group_label ?? ''),
    blank_fill: changes.kind === undefined || changes.kind === 'blank' ? page.blank_fill : 'scan',
  };
  const same =
    next.label === page.label &&
    next.kind === page.kind &&
    next.content_type === page.content_type &&
    next.content_source === page.content_source &&
    next.included === page.included &&
    next.notes === page.notes &&
    next.group_label === page.group_label &&
    next.blank_fill === page.blank_fill;
  return same ? page : next;
}
