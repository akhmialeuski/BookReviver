import { describe, expect, it } from 'vitest';
import { recipe, scan, step } from '@/features/processing/fixtures';
import {
  choiceOf,
  isSplit,
  isWide,
  offerFor,
  pagesOfScan,
  recipeFor,
  SplitChoice,
  undoesSplit,
  wideScanIds,
} from '@/features/processing/split';
import { page } from '@/features/workspace/fixtures';

describe('isWide and wideScanIds', () => {
  it('calls a scan wider than tall wide, and a square or tall one not', () => {
    expect(isWide(scan('w', 2000, 1400))).toBe(true);
    expect(isWide(scan('s', 1400, 1400))).toBe(false);
    expect(isWide(scan('t', 1000, 1500))).toBe(false);
  });

  it('collects the wide scans of a list', () => {
    const wide = wideScanIds([scan('w', 2000, 1400), scan('t', 1000, 1500), scan('x', 3000, 2000)]);

    expect([...wide].sort()).toEqual(['w', 'x']);
  });
});

describe('pagesOfScan, isSplit and choiceOf', () => {
  const pages = [
    page('right', { scan_id: 's1', slot: 2 }),
    page('left', { scan_id: 's1', slot: 1 }),
    page('whole', { scan_id: 's2', slot: 0 }),
    page('blank', { scan_id: null, slot: 0 }),
  ];

  it('gives the pages of a scan in the order of their slots', () => {
    expect(pagesOfScan(pages, 's1').map((entry) => entry.id)).toEqual(['left', 'right']);
  });

  it('tells a scan cut into halves from a scan kept whole', () => {
    expect(isSplit(pagesOfScan(pages, 's1'))).toBe(true);
    expect(isSplit(pagesOfScan(pages, 's2'))).toBe(false);
    expect(choiceOf(pagesOfScan(pages, 's1'))).toBe(SplitChoice.Two);
    expect(choiceOf(pagesOfScan(pages, 's2'))).toBe(SplitChoice.One);
  });
});

describe('offerFor', () => {
  const pages = [
    page('a1', { scan_id: 'a', slot: 1 }),
    page('a2', { scan_id: 'a', slot: 2 }),
    page('b', { scan_id: 'b', slot: 0 }),
    page('c', { scan_id: 'c', slot: 0 }),
    page('tall', { scan_id: 't', slot: 0 }),
    page('blank', { scan_id: null }),
  ];

  it('counts the wide scans, those cut already and those still whole', () => {
    const offer = offerFor(pages, new Set(['a', 'b', 'c']));

    expect(offer).toMatchObject({ wide: 3, split: 1, toCut: 2 });
  });

  it('lists the pages of the scans still whole, and no others', () => {
    expect(offerFor(pages, new Set(['a', 'b', 'c'])).pageIds).toEqual(['b', 'c']);
  });

  it('offers nothing when there is no wide scan or all are cut', () => {
    expect(offerFor(pages, new Set()).toCut).toBe(0);
    expect(offerFor(pages, new Set(['a'])).toCut).toBe(0);
  });
});

describe('recipeFor', () => {
  const whole = recipe('r-whole', { name: 'Whole scan', steps: [step('split.none')] });
  const spread = recipe('r-spread', {
    name: 'Spread',
    active: false,
    steps: [step('split.spread')],
  });

  it('finds the recipe whose first step makes the choice', () => {
    expect(recipeFor([whole, spread], SplitChoice.Two)?.id).toBe('r-spread');
    expect(recipeFor([whole, spread], SplitChoice.One)?.id).toBe('r-whole');
  });

  it('finds nothing when the stage has no such recipe', () => {
    expect(recipeFor([whole], SplitChoice.Two)).toBeUndefined();
  });
});

describe('undoesSplit', () => {
  const whole = recipe('r-whole', { steps: [step('split.none')] });
  const spread = recipe('r-spread', { steps: [step('split.spread')] });

  it('is true when the recipe keeps scans whole and a page is the half of a spread', () => {
    expect(undoesSplit(whole, [{ slot: 0 }, { slot: 1 }])).toBe(true);
  });

  it('is false when every page is a whole scan already', () => {
    expect(undoesSplit(whole, [{ slot: 0 }])).toBe(false);
  });

  it('is false for a recipe that cuts scans, whatever the pages are', () => {
    expect(undoesSplit(spread, [{ slot: 1 }, { slot: 2 }])).toBe(false);
  });
});
