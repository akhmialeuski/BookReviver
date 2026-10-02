import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import type { PageScanSchema } from '@/api';
import { scan, source } from '@/features/import/fixtures';
import { ScanGrid } from '@/features/import/ScanGrid';

/**
 * What the grid of scans shows while the page is read, when it failed, when the file has no scans, and when it has.
 */

function pageOf(items: PageScanSchema['items'], overrides: Partial<PageScanSchema> = {}) {
  return { items, total: items.length, page: 1, size: 60, pages: 1, ...overrides };
}

function render(scans: PageScanSchema | undefined, error: Error | null = null): string {
  return renderToStaticMarkup(
    <ScanGrid
      file={source('f-1', { file_name: 'Notes.pdf', scan_count: 96 })}
      scans={scans}
      error={error}
      onPage={vi.fn()}
      onOpen={vi.fn()}
    />,
  );
}

describe('ScanGrid', () => {
  it('names the file and its scan count in the title', () => {
    const markup = render(pageOf([scan('s-1')]));

    expect(markup).toContain('Scans of Notes.pdf');
    expect(markup).toContain('>96<');
  });

  it('says the page is loading before it arrives', () => {
    expect(render(undefined)).toContain('Loading…');
  });

  it('shows an alert, not the loading text, when the page could not be read', () => {
    const markup = render(undefined, new Error('The server is down'));

    expect(markup).toContain('role="alert"');
    expect(markup).not.toContain('Loading…');
  });

  it('says the scans are on their way for a file with none yet', () => {
    expect(render(pageOf([]))).toContain(
      'The scans appear here as the uploaded files are imported.',
    );
  });

  it('counts the scans from 1 and shows the label the file gives', () => {
    const markup = render(
      pageOf([scan('s-1', { number: 0 }), scan('s-2', { number: 1, source_label: 'xii' })]),
    );

    expect(markup).toContain('Scan 1');
    expect(markup).toContain('Scan 2 (xii)');
  });

  it('shows a placeholder for a scan whose images are not cut yet', () => {
    const markup = render(pageOf([scan('s-1', { images: null })]));

    expect(markup).toContain('Preparing images…');
    expect(markup).not.toContain('<img');
  });

  it('draws the thumbnail of a scan that is ready', () => {
    expect(render(pageOf([scan('s-1')]))).toContain('src="/scan-s-1/thumb"');
  });

  it('shows the pager only when the file has more than one page of scans', () => {
    expect(render(pageOf([scan('s-1')]))).not.toContain('Next');
    expect(render(pageOf([scan('s-1')], { pages: 2 }))).toContain('Next');
  });
});
