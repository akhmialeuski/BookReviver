import { describe, expect, it } from 'vitest';
import type { RecipeRuleSchema, StageSummarySchema } from '@/api';
import { recipe } from '@/features/processing/fixtures';
import {
  applyVariant,
  conditionOfKind,
  countsOf,
  markOf,
  optionsOf,
  pagesLikeKind,
  ruleFor,
  rulesOf,
  toneOf,
  VARIANT_TONES,
} from '@/features/processing/variants';
import { page, row } from '@/features/workspace/fixtures';
import { joinRows } from '@/features/workspace/strip';

const TEXT = recipe('text', { name: 'Text', active: true, created_at: '2026-10-01T00:00:00Z' });
const PLATES = recipe('plates', {
  name: 'Plates',
  active: false,
  created_at: '2026-10-01T00:01:00Z',
});
const RECIPES = [TEXT, PLATES];

function rule(id: string, overrides: Partial<RecipeRuleSchema> = {}): RecipeRuleSchema {
  return {
    id,
    project_id: 'project',
    stage: 'geometry',
    condition: 'plates',
    group_label: '',
    recipe_id: 'plates',
    order: 0,
    ...overrides,
  };
}

const ITEMS = joinRows(
  [
    page('a', { position: 0 }),
    page('b', { position: 1, kind: 'plate' }),
    page('c', { position: 2, kind: 'frontispiece' }),
    page('d', { position: 3, kind: 'plate', origin: 'placeholder' }),
    page('e', { position: 4, kind: 'cover' }),
  ],
  [
    row('a', { recipe_id: 'text' }),
    row('b', { recipe_id: 'plates', pinned: true }),
    row('c', { recipe_id: 'plates' }),
  ],
);

describe('toneOf', () => {
  it('goes by the order the variants were made in, so activating another one repaints nothing', () => {
    const activated = [
      { ...PLATES, active: true },
      { ...TEXT, active: false },
    ];

    expect(toneOf(RECIPES, 'text')).toBe(VARIANT_TONES[0]);
    expect(toneOf(RECIPES, 'plates')).toBe(VARIANT_TONES[1]);
    expect(toneOf(activated, 'text')).toBe(VARIANT_TONES[0]);
    expect(toneOf(activated, 'plates')).toBe(VARIANT_TONES[1]);
  });

  it('wraps round for a stage with more variants than colours', () => {
    const many = Array.from({ length: VARIANT_TONES.length + 1 }, (_, index) =>
      recipe(`r${index}`, { created_at: `2026-10-01T00:0${index}:00Z` }),
    );

    expect(toneOf(many, `r${VARIANT_TONES.length}`)).toBe(VARIANT_TONES[0]);
  });
});

describe('markOf', () => {
  it('names the variant of a page, with its colour and whether it is pinned', () => {
    const [, pinned, loose] = ITEMS;

    expect(pinned && markOf(RECIPES, pinned)).toEqual({
      name: 'Plates',
      tone: VARIANT_TONES[1],
      pinned: true,
    });
    expect(loose && markOf(RECIPES, loose)?.pinned).toBe(false);
  });

  it('marks no page of a stage with a single recipe, where every mark would be the same', () => {
    const [first] = ITEMS;

    expect(first && markOf([TEXT], first)).toBeNull();
  });

  it('marks no page that no recipe processed', () => {
    const [, , , , unprocessed] = ITEMS;

    expect(unprocessed && markOf(RECIPES, unprocessed)).toBeNull();
  });
});

describe('optionsOf and applyVariant', () => {
  it('counts the pages of each variant in the order they were made, and offers none for one recipe', () => {
    expect(optionsOf(RECIPES, ITEMS)).toEqual([
      { id: 'text', name: 'Text', pages: 1 },
      { id: 'plates', name: 'Plates', pages: 2 },
    ]);
    expect(optionsOf([TEXT], ITEMS)).toEqual([]);
  });

  it('keeps the pages of one variant, or every page for none', () => {
    expect(applyVariant(ITEMS, 'plates').map((item) => item.page.id)).toEqual(['b', 'c']);
    expect(applyVariant(ITEMS, null)).toBe(ITEMS);
  });
});

describe('kinds of page and the rules that name them', () => {
  it('names the condition of the kinds a rule can send, and none for the others', () => {
    expect(conditionOfKind('plate')).toBe('plates');
    expect(conditionOfKind('frontispiece')).toBe('plates');
    expect(conditionOfKind('back-cover')).toBe('covers');
    expect(conditionOfKind('blank')).toBe('blanks');
    expect(conditionOfKind('text')).toBeNull();
  });

  it('lists the pages of the same group of kinds, without a placeholder that has no image', () => {
    const [, plate] = ITEMS;

    expect(plate && pagesLikeKind(ITEMS, plate.page.kind).map((item) => item.page.id)).toEqual([
      'b',
      'c',
    ]);
    expect(pagesLikeKind(ITEMS, 'text')).toEqual([]);
  });

  it('finds the rules of a variant, and the rule of a condition and of a group', () => {
    const rules = [
      rule('r1'),
      rule('r2', { condition: 'group', group_label: 'Maps', recipe_id: 'text' }),
    ];

    expect(rulesOf(rules, 'plates').map((one) => one.id)).toEqual(['r1']);
    expect(ruleFor(rules, 'plates')?.id).toBe('r1');
    expect(ruleFor(rules, 'group', 'Maps')?.id).toBe('r2');
    expect(ruleFor(rules, 'group', 'Engravings')).toBeUndefined();
    expect(ruleFor(rules, 'covers')).toBeUndefined();
  });
});

describe('countsOf', () => {
  const summary: StageSummarySchema = {
    stage: 'geometry',
    available: true,
    manual: false,
    pages: 429,
    fresh: 426,
    stale: 0,
    failed: 0,
    not_run: 3,
    review: 0,
    check: 0,
    active_recipe_id: 'text',
    variants: [
      { recipe_id: 'text', pages: 412 },
      { recipe_id: 'plates', pages: 14 },
      { recipe_id: 'gone', pages: 3 },
    ],
  };

  it('writes the pages of each variant by name, and leaves out a recipe the stage no longer has', () => {
    expect(countsOf(summary, RECIPES, (name, pages) => `${name} ${pages}`)).toEqual([
      'Text 412',
      'Plates 14',
    ]);
  });

  it('writes nothing while the summary is read', () => {
    expect(countsOf(undefined, RECIPES, (name, pages) => `${name} ${pages}`)).toEqual([]);
  });
});
