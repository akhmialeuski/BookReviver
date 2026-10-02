import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import type { ImportResultSchema, JobSchema } from '@/api';
import { job } from '@/features/import/fixtures';
import { ImportStatus } from '@/features/projects/upload/ImportStatus';

/**
 * The report of an import that ended: the files taken in, those rejected with their reasons, those a cancelled import
 * never reached, and the reason of a failure.
 */

function render(report: JobSchema): string {
  return renderToStaticMarkup(<ImportStatus job={report} onDismiss={vi.fn()} />);
}

function ended(result: Partial<ImportResultSchema>, overrides: Partial<JobSchema> = {}) {
  return job('j-1', {
    state: 'succeeded',
    result: { imported: [], rejected: [], skipped: [], ...result },
    ...overrides,
  });
}

describe('ImportStatus', () => {
  it('counts the files taken in', () => {
    const markup = render(ended({ imported: ['a', 'b', 'c'] }));

    expect(markup).toContain('3 files imported into the book.');
    expect(markup).toContain('Finished');
  });

  it('says nothing was imported when no file got in', () => {
    expect(render(ended({}))).toContain('No file was imported.');
  });

  it('names each rejected file with the reason and its detail', () => {
    const markup = render(
      ended({
        rejected: [
          { file_name: 'book/broken.png', reason: 'unreadable', detail: 'truncated' },
          { file_name: 'book/copy.png', reason: 'duplicate', detail: '' },
        ],
      }),
    );

    expect(markup).toContain('2 files not imported');
    expect(markup).toContain('book/broken.png');
    expect(markup).toContain('Cannot be read');
    expect(markup).toContain('(truncated)');
    expect(markup).toContain('Already in the book');
  });

  it('names the files a cancelled import never reached', () => {
    const markup = render(ended({ skipped: ['late.png'] }, { state: 'cancelled' }));

    expect(markup).toContain('not reached before the import was cancelled');
    expect(markup).toContain('late.png');
    expect(markup).toContain('Cancelled');
  });

  it('gives the reason of a failed import, or a plain sentence without one', () => {
    expect(render(job('j-1', { state: 'failed', error: 'The disk is full' }))).toContain(
      'The disk is full',
    );
    expect(render(job('j-1', { state: 'failed' }))).toContain('The import failed');
  });
});
