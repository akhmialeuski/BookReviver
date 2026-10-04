import { describe, expect, it } from 'vitest';
import {
  describeContent,
  markOfContent,
  pagesToChange,
  sourcesOf,
} from '@/features/workspace/content';
import { page } from '@/features/workspace/fixtures';
import { ConditionMark } from '@/features/workspace/steps';

describe('markOfContent', () => {
  it('gives the mark of the pages of text to text, and the mark of the pictures to both pictures', () => {
    expect(markOfContent('text')).toBe(ConditionMark.Text);
    expect(markOfContent('color-picture')).toBe(ConditionMark.Picture);
    expect(markOfContent('bw-picture')).toBe(ConditionMark.Picture);
  });
});

describe('describeContent', () => {
  it('says what the page shows and where that comes from', () => {
    expect(describeContent({ content_type: 'bw-picture', content_source: 'detected' })).toBe(
      'Black-and-white picture · Found by the program',
    );
    expect(describeContent({ content_type: 'text', content_source: 'hand' })).toBe(
      'Text · Set by hand',
    );
    expect(describeContent({ content_type: 'color-picture', content_source: 'kind' })).toBe(
      'Colour picture · Given by the kind of the page',
    );
  });
});

describe('pagesToChange', () => {
  const ITEMS = [
    { page: page('a', { position: 0 }) },
    { page: page('b', { position: 1 }) },
    { page: page('c', { position: 2 }) },
    { page: page('hole', { position: 3, origin: 'placeholder', images: null }) },
  ];
  const idsOf = (selected: string[], currentId?: string): string[] =>
    pagesToChange(ITEMS, new Set(selected), currentId).map((entry) => entry.id);

  it('takes the selected pages in book order, whatever page is open', () => {
    expect(idsOf(['c', 'a'], 'b')).toEqual(['a', 'c']);
  });

  it('takes the open page when none is selected', () => {
    expect(idsOf([], 'b')).toEqual(['b']);
  });

  it('takes nothing when no page is selected or open, and never a placeholder', () => {
    expect(idsOf([])).toEqual([]);
    expect(idsOf(['hole', 'a'])).toEqual(['a']);
    expect(idsOf([], 'hole')).toEqual([]);
  });
});

describe('sourcesOf', () => {
  it('counts the pages whose type the program found and the pages whose type the reader set', () => {
    const pages = [
      page('a', { content_source: 'detected' }),
      page('b', { content_source: 'detected' }),
      page('c', { content_source: 'hand' }),
      page('d', { content_source: 'kind' }),
    ];

    expect(sourcesOf(pages)).toEqual({ found: 2, hand: 1 });
    expect(sourcesOf([])).toEqual({ found: 0, hand: 0 });
  });
});
