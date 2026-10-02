import { describe, expect, it } from 'vitest';
import { formatBytes, formatDate, formatDateTime, pluralize } from './format';

/**
 * The text helpers: sizes in the largest fitting unit, dates in a short form, and the word for a count.
 */

const BYTES_PER_KB = 1024;
const BYTES_PER_MB = BYTES_PER_KB ** 2;
const BYTES_PER_GB = BYTES_PER_KB ** 3;
const BYTES_PER_TB = BYTES_PER_KB ** 4;

describe('formatBytes', () => {
  it.each([
    [0, '0 B'],
    [1, '1 B'],
    [999, '999 B'],
    [BYTES_PER_KB, '1 KB'],
    [BYTES_PER_KB * 1.5, '1.5 KB'],
    [BYTES_PER_MB, '1 MB'],
    [BYTES_PER_MB * 12.34, '12.3 MB'],
    [BYTES_PER_GB * 4, '4 GB'],
    [BYTES_PER_TB * 2.5, '2.5 TB'],
  ])('writes %d bytes as %s', (bytes, text) => {
    expect(formatBytes(bytes)).toBe(text);
  });

  it('stays in terabytes for a size past the largest unit', () => {
    expect(formatBytes(BYTES_PER_TB * 2048)).toBe('2,048 TB');
  });

  it.each([-1, Number.NaN, Number.POSITIVE_INFINITY])(
    'writes %s, which is no size, as zero bytes',
    (bytes) => {
      expect(formatBytes(bytes)).toBe('0 B');
    },
  );
});

describe('formatDate', () => {
  it('writes the date of a timestamp in a short form', () => {
    // No offset in the text, so the date is read in the local zone and cannot move to another day
    expect(formatDate('2026-10-01T12:00:00')).toBe('Oct 1, 2026');
  });

  it('writes a text that is no date as an empty string', () => {
    expect(formatDate('yesterday')).toBe('');
    expect(formatDate('')).toBe('');
  });
});

describe('formatDateTime', () => {
  it('writes the date and the time of a timestamp', () => {
    // No offset in the text, so the moment is read in the local zone and cannot move to another day
    expect(formatDateTime('2026-10-01T12:05:00')).toMatch(/^Oct 1, 2026,? 12:05\sPM$/);
  });

  it('writes a text that is no date as an empty string', () => {
    expect(formatDateTime('yesterday')).toBe('');
  });
});

describe('pluralize', () => {
  it('gives the singular for exactly one', () => {
    expect(pluralize(1, 'file', 'files')).toBe('file');
  });

  it.each([0, 2, 5, 11, 100])('gives the plural for %d', (count) => {
    expect(pluralize(count, 'file', 'files')).toBe('files');
  });
});
