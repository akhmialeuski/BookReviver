import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PageSchema } from '@/api';
import { ContentTypeSection } from '@/features/processing/ContentTypeSection';
import { page } from '@/features/workspace/fixtures';
import { joinRows } from '@/features/workspace/strip';

/**
 * What the pages show, in the panel of a stage: the type of the selected pages is changed on every one of them at once,
 * and "Detect again" hands them to the program.
 *
 * The generated client is replaced by functions the test reads, so what the panel sends is seen as the server gets it.
 */

const sdk = vi.hoisted(() => ({ update: vi.fn(), detect: vi.fn(), jobs: vi.fn(), pages: vi.fn() }));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  updatePageApiV1ProjectsProjectIdPagesPageIdPatch: sdk.update,
  detectContentTypesApiV1ProjectsProjectIdPagesContentTypesDetectPost: sdk.detect,
  listProjectJobsApiV1ProjectsProjectIdJobsGet: sdk.jobs,
  listPagesApiV1ProjectsProjectIdPagesGet: sdk.pages,
}));

const SELECT = '[data-testid="content-type-select"]';
const DETECT = '[data-testid="content-type-detect"]';
const NO_JOBS = { data: { items: [], total: 0, page: 1, size: 20, pages: 1 } };

describe('ContentTypeSection', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  function render(pages: PageSchema[], selected: string[], currentId?: string): void {
    const items = joinRows(pages, []);
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <ContentTypeSection
            projectId="project"
            items={items}
            currentId={currentId}
            selected={new Set(selected)}
          />
        </QueryClientProvider>,
      ),
    );
  }

  function select(): HTMLSelectElement {
    const field = container.querySelector<HTMLSelectElement>(SELECT);
    if (field === null) {
      throw new Error('The section has no choice of the type.');
    }
    return field;
  }

  async function choose(value: string): Promise<void> {
    const field = select();
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set;
      setter?.call(field, value);
      field.dispatchEvent(new Event('change', { bubbles: true }));
    });
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    for (const fn of Object.values(sdk)) {
      fn.mockReset();
    }
    sdk.update.mockResolvedValue({ data: {} });
    sdk.detect.mockResolvedValue({ data: { id: 'job' } });
    sdk.jobs.mockResolvedValue(NO_JOBS);
    sdk.pages.mockResolvedValue({ data: { items: [], total: 0, page: 1, size: 100, pages: 1 } });
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

  const BOOK = [
    page('a', { position: 0, content_type: 'text', content_source: 'detected' }),
    page('b', { position: 1, content_type: 'bw-picture', content_source: 'detected' }),
    page('c', { position: 2, content_type: 'bw-picture', content_source: 'hand' }),
  ];

  it('shows the type of the open page when no page is selected, and counts where it comes from', () => {
    render(BOOK, [], 'b');

    expect(select().value).toBe('bw-picture');
    expect(container.querySelector('[data-testid="content-type-sources"]')?.textContent).toBe(
      '1 found by the program · 0 set by hand',
    );
  });

  it('shows no section when no page is open or selected', () => {
    render(BOOK, []);

    expect(container.querySelector('[data-testid="content-type"]')).toBeNull();
  });

  it('shows the type the selected pages share, and says they differ when they do not', () => {
    render(BOOK, ['b', 'c']);
    expect(select().value).toBe('bw-picture');
    expect(container.querySelector('[data-testid="content-type-pages"]')?.textContent).toBe(
      '2 selected pages',
    );

    render(BOOK, ['a', 'b']);
    expect(select().value).toBe('');
    expect(select().selectedOptions[0]?.textContent).toBe('The pages differ');
  });

  it('sends the chosen type for every selected page, as the type of the reader', async () => {
    render(BOOK, ['a', 'b']);

    await choose('color-picture');

    const sent = sdk.update.mock.calls.map((call) => call[0]);
    expect(sent).toHaveLength(2);
    expect(sent.map((options) => options.path.page_id).toSorted()).toEqual(['a', 'b']);
    expect(sent.every((options) => options.body.content_type === 'color-picture')).toBe(true);
  });

  it('sends the type for the open page when no page is selected', async () => {
    render(BOOK, [], 'a');

    await choose('bw-picture');

    expect(sdk.update).toHaveBeenCalledTimes(1);
    expect(sdk.update.mock.calls[0]?.[0]).toMatchObject({
      path: { project_id: 'project', page_id: 'a' },
      body: { content_type: 'bw-picture' },
    });
  });

  it('asks the server to detect the selected pages again, which includes those set by hand', async () => {
    render(BOOK, ['b', 'c']);

    await act(async () => {
      container.querySelector<HTMLButtonElement>(DETECT)?.click();
    });

    expect(sdk.detect).toHaveBeenCalledTimes(1);
    expect(sdk.detect.mock.calls[0]?.[0]).toMatchObject({
      path: { project_id: 'project' },
      body: { page_ids: ['b', 'c'] },
    });
  });

  it('waits while a job of the book is going', async () => {
    sdk.jobs.mockResolvedValue({
      data: {
        items: [{ id: 'running', kind: 'run-stage', state: 'running' }],
        total: 1,
        page: 1,
        size: 20,
        pages: 1,
      },
    });
    render(BOOK, ['a']);
    await act(async () => {
      await Promise.resolve();
    });

    expect(container.querySelector<HTMLButtonElement>(DETECT)?.disabled).toBe(true);
  });
});
