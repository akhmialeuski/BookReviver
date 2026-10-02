import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import type { JobSchema, SourceSchema } from '@/api';
import { FileList } from '@/features/import/FileList';
import { job, source } from '@/features/import/fixtures';

/**
 * The list of files: a row for each with its facts, the chosen one marked, and a row with a progress bar for every
 * import that is still running.
 */

function render(
  sources: SourceSchema[],
  imports: JobSchema[] = [],
  selectedId: string | undefined = undefined,
): string {
  return renderToStaticMarkup(
    <QueryClientProvider client={new QueryClient()}>
      <FileList
        projectId="p-1"
        sources={sources}
        imports={imports}
        selectedId={selectedId}
        onSelect={vi.fn()}
      />
    </QueryClientProvider>,
  );
}

describe('FileList', () => {
  it('adds up the files, the scans and the size in the summary', () => {
    const markup = render([
      source('a', { scan_count: 96, size_bytes: 1024 ** 2 }),
      source('b', { scan_count: 1, size_bytes: 1024 ** 2 }),
    ]);

    expect(markup).toContain('2 files · 97 scans · 2 MB');
  });

  it('shows the name, the kind, the scans and the size of every file', () => {
    const markup = render([
      source('a', { file_name: 'Notes.pdf', scan_count: 96, size_bytes: 1024 ** 2 }),
      source('b', { file_name: 'cover.tif', file_type: 'tiff', kind: 'image', scan_count: 1 }),
    ]);

    expect(markup).toContain('Notes.pdf');
    expect(markup).toContain('PDF document · 96 scans · 1 MB · Imported');
    expect(markup).toContain('cover.tif');
    expect(markup).toContain('TIFF image · 1 scan ·');
  });

  it('marks only the chosen file', () => {
    const markup = render([source('a'), source('b')], [], 'b');

    expect(markup.match(/aria-pressed="true"/g)).toHaveLength(1);
    expect(markup.match(/aria-pressed="false"/g)).toHaveLength(1);
  });

  it('shows the progress of a running import in a row of its own', () => {
    const markup = render([source('a')], [job('j-1')]);

    expect(markup).toContain('data-testid="import-row"');
    expect(markup).toContain('Importing 12 of 18');
    expect(markup).toContain('Importing files');
  });

  it('says a queued import waits to start', () => {
    const markup = render(
      [],
      [job('j-1', { state: 'queued', progress: { done: 0, total: 0, fraction: 0 } })],
    );

    expect(markup).toContain('Waiting to start');
    expect(markup).toContain('Starting…');
  });

  it('offers to add files', () => {
    expect(render([source('a')])).toContain('Add files');
  });
});
