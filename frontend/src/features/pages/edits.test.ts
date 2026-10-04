import { describe, expect, it } from 'vitest';
import { applyChanges } from '@/features/pages/edits';
import { page } from '@/features/workspace/fixtures';

describe('applyChanges', () => {
  const original = page('a', { label: '12', kind: 'text', included: true, notes: 'old' });

  it('changes the fields that are given and keeps the others', () => {
    expect(applyChanges(original, { kind: 'plate', included: false })).toMatchObject({
      label: '12',
      kind: 'plate',
      included: false,
      notes: 'old',
    });
  });

  it('clears a label or notes sent as null to the empty text', () => {
    expect(applyChanges(original, { label: null, notes: null })).toMatchObject({
      label: '',
      notes: '',
    });
  });

  it('keeps a label or notes that are left out, and writes ones that are empty', () => {
    expect(applyChanges(original, {})).toBe(original);
    expect(applyChanges(original, { label: '' }).label).toBe('');
  });

  it('gives back the same page when the change leaves it as it is', () => {
    expect(applyChanges(original, { kind: 'text', included: true, label: '12' })).toBe(original);
  });

  it('gives the scan back to a page with a leaf that stops being blank', () => {
    const leaf = page('b', { kind: 'blank', blank_fill: 'white' });
    expect(applyChanges(leaf, { kind: 'text' }).blank_fill).toBe('scan');
    expect(applyChanges(leaf, { kind: 'blank' })).toBe(leaf);
    expect(applyChanges(leaf, { notes: 'x' }).blank_fill).toBe('white');
  });

  it('shows a content type the reader sends as theirs, and keeps the type of a page it leaves out', () => {
    expect(applyChanges(original, { content_type: 'bw-picture' })).toMatchObject({
      content_type: 'bw-picture',
      content_source: 'hand',
    });
    expect(applyChanges(original, { kind: 'plate' })).toMatchObject({
      content_type: 'text',
      content_source: 'kind',
    });
  });

  it('gives back the same page when the type sent is the one the reader set already', () => {
    const set = page('c', { content_type: 'color-picture', content_source: 'hand' });
    expect(applyChanges(set, { content_type: 'color-picture' })).toBe(set);
  });

  it('leaves everything else of the page alone', () => {
    const changed = applyChanges(original, { label: 'iv' });
    expect({ ...changed, label: original.label }).toEqual(original);
  });
});
