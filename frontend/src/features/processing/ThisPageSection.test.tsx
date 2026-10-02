import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { EditorSession } from '@/features/editors/session';
import { SourceKind } from '@/features/processing/compare';
import { processing, version } from '@/features/processing/fixtures';
import { ThisPageSection } from '@/features/processing/ThisPageSection';
import { page, row } from '@/features/workspace/fixtures';
import type { StripItem } from '@/features/workspace/strip';
import { ProblemError } from '@/shared/http/problem';

/**
 * What the stage did to the open page: the facts the step found, the plate for a page it was unsure of, and the history
 * of the results with the choice of an earlier one.
 */

const sdk = vi.hoisted(() => ({ versions: vi.fn(), choose: vi.fn() }));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  listVersionsApiV1ProjectsProjectIdPagesPageIdVersionsGet: sdk.versions,
  chooseVersionApiV1ProjectsProjectIdPagesPageIdStagesStagePut: sdk.choose,
}));

const OLD = version('old', {
  created_at: '2026-10-01T10:00:00Z',
  params: { max_angle: 5, min_confidence: 0.3 },
});
const NEW = version('new', {
  created_at: '2026-10-01T12:00:00Z',
  params: { max_angle: 9, min_confidence: 0.3 },
  data: { angle: 1.4, confidence: 0.91 },
});

describe('ThisPageSection', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  function editorStub(overrides: Partial<EditorSession> = {}): EditorSession {
    return {
      picture: { kind: SourceKind.Iiif, url: '/info.json' },
      alwaysOn: false,
      active: false,
      hasEdit: false,
      busy: false,
      error: null,
      open: vi.fn(),
      close: vi.fn(),
      auto: vi.fn(),
      renderCanvas: () => null,
      renderPanel: () => <span data-testid="editor-own-part" />,
      ...overrides,
    };
  }

  async function render(item: StripItem, editor: EditorSession | null = null): Promise<void> {
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <ThisPageSection processing={processing()} item={item} editor={editor} />
        </QueryClientProvider>,
      );
    });
    // The versions of the page are read, which takes a few turns of the queue
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  const text = (testId: string): string =>
    container.querySelector(`[data-testid="${testId}"]`)?.textContent ?? '';

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    sdk.versions.mockReset();
    sdk.choose.mockReset();
    sdk.versions.mockResolvedValue({
      data: { items: [OLD, NEW], total: 2, page: 1, size: 100, pages: 1 },
    });
    sdk.choose.mockResolvedValue({ data: {} });
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    client.clear();
    vi.unstubAllGlobals();
  });

  it('writes what the step found with the words of the book', async () => {
    await render({ page: page('page', { label: '14' }), row: row('page', { version: NEW }) });

    expect(text('this-page')).toContain('This page · 14');
    expect(text('this-page-facts')).toContain('Turned by1.4°');
    expect(text('this-page-facts')).toContain('Confidence0.91 · sure');
  });

  it('writes the decision of the automatic split: the pages, the confidence and the slant of the cut', async () => {
    const split = version('split', {
      data: {
        pages: 2,
        confidence: 0.47,
        cut_x: 806.5,
        cut_top_x: 830,
        cut_bottom_x: 783,
        height_px: 1156,
      },
    });
    await render({ page: page('page'), row: row('page', { version: split }) });

    expect(text('this-page-facts')).toContain('Split intoTwo pages');
    expect(text('this-page-facts')).toContain('Confidence0.47 · sure');
    expect(text('this-page-facts')).toContain('Cut at807 px');
    expect(text('this-page-facts')).toContain('Slant of the cut-2.3°');
  });

  it('says why a spread was cut along a gutter that was not found for certain', async () => {
    const unsure = version('unsure', {
      data: { pages: 2, confidence: 0.04 },
      review: 'unsure-gutter',
    });
    await render({
      page: page('page'),
      row: row('page', { version: unsure, review: 'unsure-gutter' }),
    });

    expect(text('this-page-review')).toContain(
      'The gutter of this spread was not found for certain',
    );
    expect(text('this-page-facts')).toContain('0.04 · unsure');
  });

  it('puts an amber plate on a page the step was unsure of, with the way out', async () => {
    const unsure = version('unsure', {
      data: { skipped: true, confidence: 0.18 },
      review: 'not-applied',
    });
    await render({
      page: page('page'),
      row: row('page', { version: unsure, review: 'not-applied' }),
    });

    expect(text('this-page-review')).toContain('Left as it was: the step was unsure.');
    expect(text('this-page-review')).toContain('Set it by hand');
    expect(text('this-page-facts')).toContain('Left as it was');
    expect(text('this-page-facts')).toContain('0.18 · unsure');
  });

  it('names the method of the result when the stage has an editor, and not when it has none', async () => {
    const found = { page: page('page'), row: row('page', { version: NEW }) };
    await render(found);
    expect(text('this-page-facts')).not.toContain('Method');

    await render(found, editorStub());
    expect(text('this-page-facts')).toContain('MethodAutomatic');

    const byHand = version('hand', { edit_hash: 'abc', data: { angle: 2.5, confidence: 1 } });
    await render({ page: page('page'), row: row('page', { version: byHand }) }, editorStub());
    expect(text('this-page-facts')).toContain('MethodBy hand');
    expect(text('this-page-facts')).toContain('Turned by2.5°');
  });

  it('draws the controls of the editor under the facts, with the button of the plate gone', async () => {
    const unsure = version('unsure', {
      data: { skipped: true, confidence: 0.18 },
      review: 'not-applied',
    });
    const open = vi.fn();
    await render(
      { page: page('page'), row: row('page', { version: unsure, review: 'not-applied' }) },
      editorStub({ open }),
    );

    expect(container.querySelector('[data-testid="this-page-review"] button')).toBeNull();
    expect(text('editor-controls')).toContain('Set by hand');
    expect(container.querySelector('[data-testid="editor-own-part"]')).not.toBeNull();
    act(() => container.querySelector<HTMLElement>('[data-testid="editor-open"]')?.click());
    expect(open).toHaveBeenCalledTimes(1);
  });

  it('shows the controls of the editor on a page the stage has not run on yet', async () => {
    await render({ page: page('page'), row: row('page', { status: 'not-run' }) }, editorStub());

    expect(container.querySelector('[data-testid="editor-controls"]')).not.toBeNull();
  });

  it('says the stage has not run on a page that has no result', async () => {
    await render({ page: page('page'), row: row('page', { status: 'not-run' }) });

    expect(text('this-page')).toContain('This stage has not run on this page yet.');
    expect(container.querySelector('[data-testid="this-page-review"]')).toBeNull();
  });

  it('says why a page failed', async () => {
    const failed = version('failed', { state: 'failed', error: 'image unreadable' });
    await render({ page: page('page'), row: row('page', { status: 'failed', version: failed }) });

    expect(text('this-page-failed')).toBe('The step failed: image unreadable');
  });

  it('lists the results newest first, with their settings under the titles of the fields', async () => {
    await render({ page: page('page'), row: row('page', { version: NEW }) });

    const entries = [...container.querySelectorAll('[data-testid="history-entry"]')];
    expect(entries.map((entry) => entry.getAttribute('data-current'))).toEqual(['true', 'false']);
    expect(entries[0]?.textContent).toContain('Largest slant 9 · Least confidence 0.3');
    expect(entries[1]?.textContent).toContain('Largest slant 5 · Least confidence 0.3');
  });

  it('makes an earlier result the current one', async () => {
    await render({ page: page('page'), row: row('page', { version: NEW }) });

    await act(async () => {
      container.querySelector<HTMLElement>('[data-testid="history-use"]')?.click();
    });

    expect(sdk.choose).toHaveBeenCalledTimes(1);
    expect(sdk.choose.mock.calls[0]?.[0]).toMatchObject({
      path: { project_id: 'project', page_id: 'page', stage: 'geometry' },
      body: { version_id: 'old' },
    });
  });

  it('offers no button on the current result', async () => {
    await render({ page: page('page'), row: row('page', { version: NEW }) });

    const current = container.querySelector('[data-testid="history-entry"][data-current="true"]');
    expect(current?.querySelector('button')).toBeNull();
    expect(current?.textContent).toContain('Current');
  });

  it('shows the answer of the server when the choice is refused', async () => {
    sdk.choose.mockRejectedValue(
      new ProblemError('Another job of this book is running.', 409, null, []),
    );
    await render({ page: page('page'), row: row('page', { version: NEW }) });

    await act(async () => {
      container.querySelector<HTMLElement>('[data-testid="history-use"]')?.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(container.querySelector('[role="alert"]')?.textContent).toContain(
      'Another job of this book is running.',
    );
  });
});
