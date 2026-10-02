import { describe, expect, it } from 'vitest';
import { commonOf, spanOf } from '@/features/order/summary';
import { page } from '@/features/workspace/fixtures';

describe('commonOf', () => {
  it('gives the value all the pages share', () => {
    expect(commonOf(['text', 'text'])).toBe('text');
    expect(commonOf([false])).toBe(false);
  });

  it('gives null when the values differ or there are none', () => {
    expect(commonOf(['text', 'plate'])).toBeNull();
    expect(commonOf([true, false, true])).toBeNull();
    expect(commonOf([])).toBeNull();
  });

  it('tells a shared empty text from none', () => {
    expect(commonOf(['', ''])).toBe('');
  });
});

describe('spanOf', () => {
  it('writes the span of numbered pages', () => {
    const pages = [
      page('a', { label: '44' }),
      page('b', { label: '45' }),
      page('c', { label: '46' }),
    ];
    expect(spanOf(pages)).toBe('p. 44–46');
  });

  it('writes one page alone', () => {
    expect(spanOf([page('a', { label: 'xii' })])).toBe('p. xii');
  });

  it('falls back to the places in the book when an end has no number', () => {
    const pages = [page('a', { position: 4 }), page('b', { position: 6, label: '7' })];
    expect(spanOf(pages)).toBe('#5–7');
  });

  it('writes nothing for no pages', () => {
    expect(spanOf([])).toBe('');
  });
});
