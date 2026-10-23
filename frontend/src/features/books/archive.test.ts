import { describe, expect, it } from 'vitest';
import { archiveUrl } from '@/features/books/archive';

/** The address a link downloads the archive of a book from. */

describe('archiveUrl', () => {
  it('names the archive route of the book', () => {
    expect(archiveUrl('9a5aa8f3-be22-48eb-a75d-bd5403cce119')).toBe(
      '/api/v1/books/9a5aa8f3-be22-48eb-a75d-bd5403cce119/archive',
    );
  });
});
