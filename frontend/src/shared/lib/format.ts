/**
 * Numbers and dates as the interface writes them.
 *
 * The wording around them lives in `messages.ts`, so these helpers only turn a value into the short text a sentence
 * needs. They never throw on a value they cannot format, because a label must not take a screen down.
 */

const BYTES_PER_UNIT = 1024;
const UNITS = ['B', 'KB', 'MB', 'GB', 'TB'] as const;
const MAX_FRACTION_DIGITS = 1;
const LOCALE = 'en';
const NOT_A_DATE = '';

const sizeFormat = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: MAX_FRACTION_DIGITS });
const dateFormat = new Intl.DateTimeFormat(LOCALE, { dateStyle: 'medium' });

/**
 * Write a size in bytes with the largest unit that keeps the number at or above one.
 *
 * Units go up by 1024 and the number keeps at most one decimal, so `1536` is `1.5 KB` and `0` is `0 B`. A negative
 * or non-finite size is written as `0 B`.
 */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return `${sizeFormat.format(0)} ${UNITS[0]}`;
  }
  const unit = Math.min(Math.floor(Math.log(bytes) / Math.log(BYTES_PER_UNIT)), UNITS.length - 1);
  return `${sizeFormat.format(bytes / BYTES_PER_UNIT ** unit)} ${UNITS[unit]}`;
}

/**
 * Write the date of an ISO 8601 timestamp the way a sentence quotes it, such as `Oct 1, 2026`.
 *
 * A text that is no date gives an empty string.
 */
export function formatDate(timestamp: string): string {
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime()) ? NOT_A_DATE : dateFormat.format(date);
}

/** Pick the singular word for exactly one and the plural word for every other count. */
export function pluralize(count: number, singular: string, plural: string): string {
  return count === 1 ? singular : plural;
}
