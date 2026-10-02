import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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

  async function render(item: StripItem): Promise<void> {
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <ThisPageSection processing={processing()} item={item} />
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
    expect(
      container.querySelector<HTMLButtonElement>('[data-testid="this-page-review"] button')
        ?.disabled,
    ).toBe(true);
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
