import { describe, expect, it } from 'vitest';
import { describePageError } from '@/features/pages/errors';
import { parseProblem } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';

describe('describePageError', () => {
  it('names the conflict and quotes the reason the server gave', () => {
    const error = parseProblem(
      { status: 409, detail: 'The anchor is one of the moved pages.' },
      409,
    );

    expect(describePageError(error)).toBe(
      MESSAGES.pages.conflict('The anchor is one of the moved pages.'),
    );
  });

  it.each([
    'The pages changed while this ran. The book now shows them as they are. Try again.',
    'The numbering runs from a later page to an earlier one.',
    'Only a missing page can take a scan.',
    'This scan is already a page of the book: p. 12.',
  ])('quotes the sentence the server gives for a conflict of pages: %s', (detail) => {
    const error = parseProblem({ status: 409, detail }, 409);

    expect(describePageError(error)).toBe(MESSAGES.pages.conflict(detail));
  });

  it('names the conflict alone when the server gave no reason', () => {
    expect(describePageError(parseProblem({ status: 409 }, 409))).toBe(MESSAGES.pages.conflict(''));
  });

  it('leaves the usual message for every other failure', () => {
    expect(describePageError(parseProblem({ status: 404 }, 404))).toBe(MESSAGES.problems.notFound);
    expect(describePageError(parseProblem(new TypeError('failed'), null))).toBe(
      MESSAGES.problems.network,
    );
    expect(describePageError('anything')).toBe(MESSAGES.problems.unknown);
  });
});
