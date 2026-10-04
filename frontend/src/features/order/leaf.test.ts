import { describe, expect, it } from 'vitest';
import { leafPages, leavesLost } from '@/features/order/leaf';
import { page } from '@/features/workspace/fixtures';

describe('leafPages', () => {
  it('keeps the blank pages cut from a scan and leaves out every other page', () => {
    const pages = [
      page('text', { kind: 'text' }),
      page('blank', { kind: 'blank' }),
      page('generated', { kind: 'blank', origin: 'blank' }),
      page('waiting', { kind: 'blank', origin: 'placeholder' }),
    ];
    expect(leafPages(pages).map((entry) => entry.id)).toEqual(['blank']);
  });
});

describe('leavesLost', () => {
  const pages = [
    page('scan', { kind: 'blank', blank_fill: 'scan' }),
    page('white', { kind: 'blank', blank_fill: 'white' }),
    page('paper', { kind: 'blank', blank_fill: 'paper' }),
  ];

  it('counts the pages with a leaf when the kind stops being blank', () => {
    expect(leavesLost(pages, 'text')).toBe(2);
  });

  it('counts none when the pages stay blank', () => {
    expect(leavesLost(pages, 'blank')).toBe(0);
  });
});
