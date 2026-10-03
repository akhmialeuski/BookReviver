import { describe, expect, it } from 'vitest';
import { version } from '@/features/processing/fixtures';
import { reasonOf } from '@/features/processing/reasons';
import { page, row } from '@/features/workspace/fixtures';

function item(overrides: Parameters<typeof row>[1] = {}) {
  return { page: page('a'), row: row('a', overrides) };
}

describe('reasonOf', () => {
  it('says why a page failed, with what the step said', () => {
    const failed = item({ status: 'failed', version: version('v', { error: 'image unreadable' }) });

    expect(reasonOf(failed)).toBe('Failed: image unreadable');
  });

  it('says only that a page failed when the step gave no reason', () => {
    expect(reasonOf(item({ status: 'failed' }))).toBe('Failed');
  });

  it('says that a result is out of date', () => {
    expect(reasonOf(item({ status: 'stale' }))).toBe('Out of date');
  });

  it('says the step left the page as it was, with its confidence', () => {
    const left = item({
      review: 'not-applied',
      version: version('v', { data: { confidence: 0.18 } }),
    });

    expect(reasonOf(left)).toBe('Left as it was · 0.18');
  });

  it('says the step was unsure, with its confidence when it has one', () => {
    const unsure = item({
      review: 'low-confidence',
      version: version('v', { data: { confidence: 0.4 } }),
    });

    expect(reasonOf(unsure)).toBe('Unsure · 0.40');
    expect(reasonOf(item({ review: 'low-confidence' }))).toBe('Unsure');
  });

  it('says the gutter of a spread was not found for certain, and that a narrow scan has one', () => {
    const unsure = item({
      review: 'unsure-gutter',
      version: version('v', { data: { confidence: 0.04 } }),
    });

    expect(reasonOf(unsure)).toBe('Gutter not found for certain · 0.04');
    expect(reasonOf(item({ review: 'unsure-gutter' }))).toBe('Gutter not found for certain');
    expect(reasonOf(item({ review: 'narrow-gutter' }))).toBe(
      'Narrow scan with a gutter in the middle',
    );
  });

  it('says the text may be cut by the edge of the scan', () => {
    expect(reasonOf(item({ review: 'cut-by-edge' }))).toBe(
      'Text may be cut by the edge of the scan',
    );
  });

  it('says the text of the page differs too much in size from the text of the book', () => {
    expect(reasonOf(item({ review: 'size-differs' }))).toBe(
      'The text of this page differs too much in size',
    );
  });

  it('says the lines of a page were too few to dewarp it, or are still bent after it', () => {
    expect(reasonOf(item({ review: 'few-lines' }))).toBe(
      'Too few lines to tell how the page is bent',
    );
    expect(reasonOf(item({ review: 'high-residual' }))).toBe('Lines still bent after dewarping');
  });

  it('puts a failure before a mark of review, since a failed page has no result to doubt', () => {
    expect(reasonOf(item({ status: 'failed', review: 'low-confidence' }))).toBe('Failed');
  });

  it('gives no reason to a page that needs no look or has no row yet', () => {
    expect(reasonOf(item())).toBeNull();
    expect(reasonOf({ page: page('a'), row: undefined })).toBeNull();
  });
});
