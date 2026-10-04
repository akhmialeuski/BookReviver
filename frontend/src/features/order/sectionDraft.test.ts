import { describe, expect, it } from 'vitest';
import { draftOf, newSectionDraft, sectionBody } from '@/features/order/sectionDraft';
import { section } from '@/features/workspace/fixtures';

/** The form of a section and the body the server takes for it. */

describe('newSectionDraft', () => {
  it('starts a section of the main flow at the page, printing Arabic numbers from 1', () => {
    expect(newSectionDraft('p3')).toEqual({
      firstId: 'p3',
      name: '',
      style: 'arabic',
      start: 1,
      prefix: '',
      display: 'printed',
      kinds: [],
    });
  });
});

describe('draftOf', () => {
  it('fills the form with the rule of a section that exists', () => {
    const plates = section('s1', 'p2', {
      name: 'Plates',
      style: 'roman-upper',
      start: 3,
      prefix: 'Plate ',
      display: 'counted',
      kinds: ['plate', 'frontispiece'],
    });

    expect(draftOf(plates)).toEqual({
      firstId: 'p2',
      name: 'Plates',
      style: 'roman-upper',
      start: 3,
      prefix: 'Plate ',
      display: 'counted',
      kinds: ['plate', 'frontispiece'],
    });
  });
});

describe('sectionBody', () => {
  it('writes the form as the body of the server, trimming the name and keeping the spaces of the prefix', () => {
    const body = sectionBody({
      ...newSectionDraft('p1'),
      name: '  Preface ',
      style: 'roman-lower',
      start: 6,
      prefix: 'Plate ',
      display: 'counted',
      kinds: ['plate'],
    });

    expect(body).toEqual({
      first_page_id: 'p1',
      name: 'Preface',
      style: 'roman-lower',
      start: 6,
      prefix: 'Plate ',
      display: 'counted',
      kinds: ['plate'],
    });
  });

  it('keeps the first number within what the style can write', () => {
    expect(sectionBody({ ...newSectionDraft('p1'), style: 'roman-upper', start: 9999 }).start).toBe(
      3999,
    );
    expect(sectionBody({ ...newSectionDraft('p1'), start: 0 }).start).toBe(1);
  });
});
