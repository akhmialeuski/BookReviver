import { describe, expect, it } from 'vitest';
import {
  findGaps,
  formatNumber,
  hiddenGaps,
  type LabelledPage,
  missingCount,
  parseLabel,
  withLabels,
} from '@/features/pages/gaps';

/** Pages with ids `a`, `b`, `c` and so on, one for each label; a label starting with `~` is left out of the book. */
function pagesOf(...labels: string[]): LabelledPage[] {
  return labels.map((label, index) => ({
    id: String.fromCharCode(97 + index),
    label: label.replace(/^~/, ''),
    included: !label.startsWith('~'),
  }));
}

describe('parseLabel', () => {
  it('reads Arabic numbers', () => {
    expect(parseLabel('47')).toEqual({ style: 'arabic', value: 47 });
    expect(parseLabel(' 7 ')).toEqual({ style: 'arabic', value: 7 });
  });

  it('reads Roman numbers in both cases', () => {
    expect(parseLabel('xiv')).toEqual({ style: 'roman-lower', value: 14 });
    expect(parseLabel('XLIX')).toEqual({ style: 'roman-upper', value: 49 });
    expect(parseLabel('mmcmxcix')).toEqual({ style: 'roman-lower', value: 2999 });
  });

  it('reads a number in square brackets as the number', () => {
    expect(parseLabel('[1]')).toEqual({ style: 'arabic', value: 1 });
    expect(parseLabel('[iv]')).toEqual({ style: 'roman-lower', value: 4 });
  });

  it('reads nothing from an empty label, a zero, a mixed case or text that is no number', () => {
    for (const label of ['', '   ', '0', 'Xi', 'iiii', 'vx', 'ill.', '12a', '[]', 'IIX']) {
      expect(parseLabel(label)).toBeNull();
    }
  });
});

describe('formatNumber', () => {
  it('writes a number the way the server writes a label', () => {
    expect(formatNumber('arabic', 48)).toBe('48');
    expect(formatNumber('roman-lower', 48)).toBe('xlviii');
    expect(formatNumber('roman-upper', 1994)).toBe('MCMXCIV');
  });

  it('writes back what parseLabel reads, for every number the server numbers in Roman', () => {
    for (let value = 1; value <= 3999; value += 1) {
      expect(parseLabel(formatNumber('roman-lower', value))).toEqual({
        style: 'roman-lower',
        value,
      });
    }
  });
});

describe('findGaps', () => {
  it('finds nothing in an unbroken run', () => {
    expect(findGaps(pagesOf('1', '2', '3', '4'))).toEqual([]);
  });

  it('finds the numbers an Arabic jump skips', () => {
    expect(findGaps(pagesOf('44', '45', '46', '49', '50'))).toEqual([
      {
        afterPageId: 'c',
        beforePageId: 'd',
        style: 'arabic',
        jumpFrom: 46,
        jumpTo: 49,
        firstMissing: 47,
        lastMissing: 48,
      },
    ]);
  });

  it('counts the pages a gap lacks', () => {
    const [gap] = findGaps(pagesOf('46', '49'));
    expect(gap === undefined ? 0 : missingCount(gap)).toBe(2);
    const [single] = findGaps(pagesOf('46', '48'));
    expect(single === undefined ? 0 : missingCount(single)).toBe(1);
  });

  it('finds the numbers a Roman jump skips, in either case', () => {
    expect(findGaps(pagesOf('i', 'ii', 'v'))).toMatchObject([
      { style: 'roman-lower', firstMissing: 3, lastMissing: 4 },
    ]);
    expect(findGaps(pagesOf('IX', 'XII'))).toMatchObject([
      { style: 'roman-upper', firstMissing: 10, lastMissing: 11 },
    ]);
  });

  it('reads bracketed numbers as the numbers they hold', () => {
    expect(findGaps(pagesOf('[1]', '[2]', '[5]'))).toMatchObject([
      { afterPageId: 'b', beforePageId: 'c', firstMissing: 3, lastMissing: 4 },
    ]);
    expect(findGaps(pagesOf('[1]', '2', '3'))).toEqual([]);
  });

  it('makes no gap where the style changes', () => {
    expect(findGaps(pagesOf('i', 'ii', 'iii', '1', '2'))).toEqual([]);
    expect(findGaps(pagesOf('iii', 'I', 'II'))).toEqual([]);
  });

  it('makes no gap where the number does not rise', () => {
    expect(findGaps(pagesOf('5', '6', '1', '2', '2'))).toEqual([]);
  });

  it('lets a page without a number stand for a number, as a plate does in a printed book', () => {
    expect(findGaps(pagesOf('46', '', '48'))).toEqual([]);
    expect(findGaps(pagesOf('46', '', '', '49'))).toEqual([]);
  });

  it('tells only the numbers the unnumbered pages cannot hold', () => {
    expect(findGaps(pagesOf('46', '', '50'))).toMatchObject([
      { afterPageId: 'a', beforePageId: 'c', firstMissing: 48, lastMissing: 49 },
    ]);
  });

  it('sees through the pages of a kind the numbering skips, which have no label', () => {
    // A cover, a title page with its number in brackets, a plate, and the first numbered page after them
    expect(findGaps(pagesOf('', '[1]', '', '3', '4'))).toEqual([]);
    expect(findGaps(pagesOf('', '[1]', '', '4', '5'))).toMatchObject([
      { firstMissing: 3, lastMissing: 3 },
    ]);
  });

  it('skips pages left out of the book, so they neither hold numbers nor break a run', () => {
    expect(findGaps(pagesOf('46', '~', '~47', '47'))).toEqual([]);
    expect(findGaps(pagesOf('46', '~', '48'))).toMatchObject([{ firstMissing: 47 }]);
  });

  it('keeps a text that is no number from breaking a run, as an unnumbered page does not', () => {
    expect(findGaps(pagesOf('3', 'ill.', '5'))).toEqual([]);
  });

  it('finds every gap of a book', () => {
    expect(findGaps(pagesOf('1', '3', '4', '8'))).toMatchObject([
      { firstMissing: 2, lastMissing: 2 },
      { firstMissing: 5, lastMissing: 7 },
    ]);
  });

  it('finds nothing in a book without numbers or with one numbered page', () => {
    expect(findGaps([])).toEqual([]);
    expect(findGaps(pagesOf('', '', ''))).toEqual([]);
    expect(findGaps(pagesOf('', '7', ''))).toEqual([]);
  });
});

describe('hiddenGaps', () => {
  const current = findGaps(pagesOf('44', '45', '46', '49', '50'));

  it('picks the gap a numbering closes', () => {
    const proposed = findGaps(pagesOf('44', '45', '46', '47', '48'));
    expect(hiddenGaps(current, proposed)).toEqual(current);
  });

  it('picks nothing when the gap is still there, whatever the numbers beside it say', () => {
    const proposed = findGaps(pagesOf('1', '2', '3', '9', '10'));
    expect(hiddenGaps(current, proposed)).toEqual([]);
  });

  it('picks nothing when there was no gap', () => {
    expect(hiddenGaps([], current)).toEqual([]);
  });
});

describe('withLabels', () => {
  it('puts the new labels on the pages that get one and leaves the rest', () => {
    const pages = pagesOf('5', '', '7');
    const labels = new Map([
      ['a', '1'],
      ['c', ''],
    ]);
    expect(withLabels(pages, labels).map((page) => page.label)).toEqual(['1', '', '']);
  });

  it('gives back the same page object where nothing changes', () => {
    const pages = pagesOf('5', '6');
    expect(withLabels(pages, new Map([['a', '1']]))[1]).toBe(pages[1]);
  });
});
