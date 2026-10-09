import type { PageStepSettingsSchema, RecipeKind, Stage, ValueForm } from '@/api';
import type { StripItem } from '@/features/workspace/strip';
import { MESSAGES } from '@/shared/messages';

/**
 * The values a setting of a step has for a part of the pages: what the chips under a setting and the menu that adds one
 * are drawn from.
 *
 * A setting has the value of the recipe, and a part of the pages may have a value of its own: the open page, the pages
 * selected in the grid, the odd pages, the even pages or a group. A page takes the value of the strongest part it is in,
 * a page before a group, a group before a side, a side before the recipe. The server works that out for a run and for
 * the effective value it lists, and nothing here repeats it.
 */

const labels = MESSAGES.processing.steps.values;

/** The pages a value is for, as the server takes them. */
export type ValueTarget = Omit<ValueForm, 'value'>;

/** The open page and everything the values of parts of the pages are told from it. */
export interface PageValues {
  projectId: string;
  stage: Stage;
  /** The open page. */
  page: { id: string; name: string };
  /** What the open page and the parts of the pages it is in change, one entry for each step that has any. */
  settings: readonly PageStepSettingsSchema[];
  /** The pages selected in the grid. */
  selected: readonly string[];
  /** How many pages of the recipe are odd and how many are even. */
  sides: { odd: number; even: number };
  /** The groups of the pages of the recipe, with the pages each has. */
  groups: readonly { label: string; pages: number }[];
}

/** One value for a part of the pages, as a chip under the setting. */
export interface ValueChip {
  /** What tells the chip from the others of the setting. */
  id: string;
  target: ValueTarget;
  title: string;
  value: unknown;
  /** Whether the chip is the value of the open page, which is drawn apart. */
  own: boolean;
}

/** One part of the pages the menu offers a value for. */
export interface ValueChoice {
  id: string;
  title: string;
  /** How many pages the value would be for. */
  pages: number;
  target: ValueTarget;
  /** Whether the part has a value of the setting already, which a chip shows. */
  taken: boolean;
}

/** The menu that adds a value: the parts of the book that are told apart, and the groups. */
export interface ValueChoices {
  parts: readonly ValueChoice[];
  groups: readonly ValueChoice[];
}

/** Find what a step has for the open page and the parts of the pages it is in. */
export function settingsOf(
  settings: readonly PageStepSettingsSchema[] | undefined,
  stepId: string | null,
): PageStepSettingsSchema | undefined {
  return stepId === null ? undefined : settings?.find((entry) => entry.step_id === stepId);
}

/** Write the value of a field as a sentence quotes it: a text as it is, anything else as JSON. */
export function showValue(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value);
}

/** Name the open page the way a chip does: by its printed number, or by its place when it has none. */
export function pageName(label: string, position: number): string {
  return label === '' ? labels.pageAt(position + 1) : labels.pageName(label);
}

/**
 * Count the pages of a recipe by the part of the book they are in.
 *
 * @param items The pages of the book with where each stands in the stage.
 * @param kind The kind of page the recipe processes, or undefined while the recipe is read.
 * @returns How many pages are odd and even, and how many each group has, the groups in the order the book meets them.
 */
export function countParts(
  items: readonly StripItem[],
  kind: RecipeKind | undefined,
): Pick<PageValues, 'sides' | 'groups'> {
  const sides = { odd: 0, even: 0 };
  const groups = new Map<string, number>();
  for (const { page, row } of items) {
    if (kind === undefined || row?.kind !== kind) {
      continue;
    }
    // An odd place of the book is a right page, as the server counts it
    sides[(page.position + 1) % 2 === 1 ? 'odd' : 'even'] += 1;
    if (page.group_label !== '') {
      groups.set(page.group_label, (groups.get(page.group_label) ?? 0) + 1);
    }
  }
  return {
    sides,
    groups: [...groups].map(([label, pages]) => ({ label, pages })),
  };
}

/**
 * List the values a step has for a field, weakest part first: the odd and the even pages, the groups, then the page.
 *
 * @param values The open page and its settings.
 * @param stepId The saved step.
 * @param name The name of the field.
 */
export function chipsOf(values: PageValues, stepId: string, name: string): ValueChip[] {
  const entry = settingsOf(values.settings, stepId);
  if (entry === undefined) {
    return [];
  }
  const chips: ValueChip[] = entry.parts
    .filter((part) => name in part.params)
    .map((part) => ({
      id: part.scope === 'group' ? `group|${part.group_label}` : part.scope,
      target: { scope: part.scope, group_label: part.group_label },
      title: part.scope === 'group' ? labels.groupOf(part.group_label) : labels.scopes[part.scope],
      value: part.params[name],
      own: false,
    }))
    .sort((a, b) => ORDER.indexOf(a.target.scope) - ORDER.indexOf(b.target.scope));
  if (name in entry.params) {
    chips.push({
      id: 'page',
      target: { scope: 'pages', page_ids: [values.page.id] },
      title: values.page.name,
      value: entry.params[name],
      own: true,
    });
  }
  return chips;
}

/** The scopes of the chips from the weakest to the strongest, as they stand under a setting. */
const ORDER: readonly ValueForm['scope'][] = ['odd', 'even', 'group', 'pages'];

/**
 * List the parts of the pages a value of a field can be added for.
 *
 * @param values The open page and its settings.
 * @param stepId The saved step.
 * @param name The name of the field.
 */
export function choicesOf(values: PageValues, stepId: string, name: string): ValueChoices {
  const taken = new Set(chipsOf(values, stepId, name).map((chip) => chip.id));
  const choice = (id: string, title: string, pages: number, target: ValueTarget): ValueChoice => ({
    id,
    title,
    pages,
    target,
    taken: taken.has(id),
  });
  return {
    parts: [
      choice('page', labels.thisPage(values.page.name), 1, {
        scope: 'pages',
        page_ids: [values.page.id],
      }),
      choice('selected', labels.scopes.selected, values.selected.length, {
        scope: 'pages',
        page_ids: [...values.selected],
      }),
      choice('odd', labels.scopes.odd, values.sides.odd, { scope: 'odd' }),
      choice('even', labels.scopes.even, values.sides.even, { scope: 'even' }),
    ],
    groups: values.groups.map(({ label, pages }) =>
      choice(`group|${label}`, labels.groupOf(label), pages, {
        scope: 'group',
        group_label: label,
      }),
    ),
  };
}
