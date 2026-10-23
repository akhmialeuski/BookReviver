import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import type { JobSchema, SourceSchema } from '@/api';
import { ImportStatus } from '@/features/books/upload/ImportStatus';
import { job, unimported } from '@/features/import/fixtures';

/**
 * The report of the imports that ended: the files rejected with their reasons, those a cancelled import never reached,
 * and the reason of a failure.
 */

/** A button whose disabled attribute is set, as the static markup writes it. */
const DISABLED_BUTTON = /<button[^>]* disabled=""/;

function render(
  files: readonly SourceSchema[],
  failed: JobSchema | undefined = undefined,
  closing = false,
): string {
  return renderToStaticMarkup(
    <ImportStatus failed={failed} files={files} closing={closing} onDismiss={vi.fn()} />,
  );
}

describe('ImportStatus', () => {
  it('names each rejected file with the reason and its detail', () => {
    const markup = render([
      unimported('f-1', 'rejected', {
        file_name: 'book/broken.png',
        rejection: 'unreadable',
        detail: 'truncated',
      }),
      unimported('f-2', 'rejected', { file_name: 'book/copy.png', rejection: 'duplicate' }),
    ]);

    expect(markup).toContain('2 files not imported');
    expect(markup).toContain('book/broken.png');
    expect(markup).toContain('Cannot be read');
    expect(markup).toContain('(truncated)');
    expect(markup).toContain('Already in the book');
  });

  it('names the files a cancelled import never reached', () => {
    const markup = render([unimported('f-1', 'skipped', { file_name: 'late.png' })]);

    expect(markup).toContain('not reached before the import was cancelled');
    expect(markup).toContain('late.png');
  });

  it('gives the reason of a failed import, or a plain sentence without one', () => {
    expect(render([], job('j-1', { state: 'failed', error: 'The disk is full' }))).toContain(
      'The disk is full',
    );
    expect(render([], job('j-1', { state: 'failed' }))).toContain('The import failed');
  });

  it('disables the dismissal while the server deletes the files', () => {
    // The attribute itself, since the classes of every button name the disabled variants of its styles
    expect(render([unimported('f-1', 'skipped')], undefined, true)).toMatch(DISABLED_BUTTON);
    expect(render([unimported('f-1', 'skipped')])).not.toMatch(DISABLED_BUTTON);
  });
});
