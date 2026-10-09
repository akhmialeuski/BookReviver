import { describe, expect, it } from 'vitest';
import { pageValues, stepSettings } from '@/features/processing/fixtures';
import {
  chipsOf,
  choicesOf,
  countParts,
  pageName,
  settingsOf,
  showValue,
} from '@/features/processing/pageSettings';
import type { StripItem } from '@/features/workspace/strip';

/** The values a setting has for parts of the pages, read from the settings the server lists, and the menu that adds one. */

const STEP = 'step';
const PART = { updated_at: '2026-10-01T00:00:00Z' };

function item(position: number, kind: string, group = ''): StripItem {
  return {
    page: { id: `p${position}`, position, group_label: group },
    row: { kind },
  } as unknown as StripItem;
}

describe('settingsOf', () => {
  const listed = [stepSettings('a'), stepSettings('b', { params: { depth: 2 } })];

  it('finds what the page and its parts have for the step', () => {
    expect(settingsOf(listed, 'b')?.params).toEqual({ depth: 2 });
  });

  it('gives nothing for a step the page has no value for, a step that is not saved, and settings not read yet', () => {
    expect(settingsOf(listed, 'c')).toBeUndefined();
    expect(settingsOf(listed, null)).toBeUndefined();
    expect(settingsOf(undefined, 'a')).toBeUndefined();
  });
});

describe('countParts', () => {
  const items = [
    item(0, 'text'),
    item(1, 'text', 'Index'),
    item(2, 'text', 'Index'),
    item(3, 'blank', 'Index'),
    item(4, 'text'),
  ];

  it('counts the odd and the even places of the pages of the kind and not those of another kind, a right page being at an odd place', () => {
    expect(countParts(items, 'text').sides).toEqual({ odd: 3, even: 1 });
  });

  it('counts the pages of each group of the kind, and no page of another kind', () => {
    expect(countParts(items, 'text').groups).toEqual([{ label: 'Index', pages: 2 }]);
  });

  it('counts nothing while the recipe is read', () => {
    expect(countParts(items, undefined)).toEqual({ sides: { odd: 0, even: 0 }, groups: [] });
  });
});

describe('chipsOf', () => {
  const values = pageValues({
    settings: [
      stepSettings(STEP, {
        params: { depth: 4 },
        parts: [
          { scope: 'group', group_label: 'Index', params: { depth: 1 }, ...PART },
          { scope: 'even', group_label: '', params: { depth: 3, other: 9 }, ...PART },
          { scope: 'odd', group_label: '', params: { other: 8 }, ...PART },
        ],
      }),
    ],
  });

  it('lists the values of a field from the weakest part to the open page, which is drawn apart', () => {
    expect(
      chipsOf(values, STEP, 'depth').map(({ title, value, own }) => [title, value, own]),
    ).toEqual([
      ['Even pages', 3, false],
      ['Group · Index', 1, false],
      ['p. 143', 4, true],
    ]);
  });

  it('lists only the values of that field', () => {
    expect(chipsOf(values, STEP, 'other').map((chip) => chip.title)).toEqual([
      'Odd pages',
      'Even pages',
    ]);
  });

  it('names what each chip takes back: the page by its identifier and a group by its label', () => {
    expect(chipsOf(values, STEP, 'depth').map((chip) => chip.target)).toEqual([
      { scope: 'even', group_label: '' },
      { scope: 'group', group_label: 'Index' },
      { scope: 'pages', page_ids: ['page-143'] },
    ]);
  });

  it('lists nothing for a step with no value', () => {
    expect(chipsOf(values, 'other-step', 'depth')).toEqual([]);
  });
});

describe('choicesOf', () => {
  const values = pageValues({
    selected: ['a', 'b', 'c'],
    sides: { odd: 305, even: 304 },
    groups: [{ label: 'Index', pages: 12 }],
    settings: [
      stepSettings(STEP, {
        parts: [{ scope: 'even', group_label: '', params: { depth: 3 }, ...PART }],
      }),
    ],
  });

  it('offers the page, the selected pages, the odd and the even pages with the pages each covers', () => {
    expect(
      choicesOf(values, STEP, 'depth').parts.map(({ id, title, pages }) => [id, title, pages]),
    ).toEqual([
      ['page', 'This page · p. 143', 1],
      ['selected', 'Selected pages', 3],
      ['odd', 'Odd pages', 305],
      ['even', 'Even pages', 304],
    ]);
  });

  it('offers each group of the book with its pages', () => {
    expect(
      choicesOf(values, STEP, 'depth').groups.map(({ title, pages }) => [title, pages]),
    ).toEqual([['Group · Index', 12]]);
  });

  it('marks the part that has a value of the field already, and no other', () => {
    const taken = choicesOf(values, STEP, 'depth').parts.filter((choice) => choice.taken);
    expect(taken.map((choice) => choice.id)).toEqual(['even']);
    expect(choicesOf(values, STEP, 'other').parts.some((choice) => choice.taken)).toBe(false);
  });

  it('sends the selected pages as they are chosen', () => {
    expect(choicesOf(values, STEP, 'depth').parts[1]?.target).toEqual({
      scope: 'pages',
      page_ids: ['a', 'b', 'c'],
    });
  });
});

describe('pageName', () => {
  it('names a page by its printed number, or by its place when it has none', () => {
    expect(pageName('143', 150)).toBe('p. 143');
    expect(pageName('', 4)).toBe('page 5');
  });
});

describe('showValue', () => {
  it('writes a text as it is and anything else as JSON', () => {
    expect([showValue('otsu'), showValue(0.5), showValue([1, 2])]).toEqual([
      'otsu',
      '0.5',
      '[1,2]',
    ]);
  });
});
