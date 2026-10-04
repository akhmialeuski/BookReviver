import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { EditorSession } from '@/features/editors/session';
import { SourceKind } from '@/features/processing/compare';
import { processing, recipe, step, version } from '@/features/processing/fixtures';
import { ThisPageSection } from '@/features/processing/ThisPageSection';
import { page, row } from '@/features/workspace/fixtures';
import type { StripItem } from '@/features/workspace/strip';
import { ProblemError } from '@/shared/http/problem';

/**
 * What the stage did to the open page: the facts the step found, the plate for a page it was unsure of, and the history
 * of the results with the choice of an earlier one.
 */

const sdk = vi.hoisted(() => ({
  versions: vi.fn(),
  choose: vi.fn(),
  remake: vi.fn(),
  mark: vi.fn(),
  jobs: vi.fn(),
}));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  listVersionsApiV1ProjectsProjectIdPagesPageIdVersionsGet: sdk.versions,
  chooseVersionApiV1ProjectsProjectIdPagesPageIdStagesStagePut: sdk.choose,
  remakeVersionApiV1ProjectsProjectIdPagesPageIdVersionsVersionIdRemakePost: sdk.remake,
  putMarkApiV1ProjectsProjectIdPagesPageIdVersionsVersionIdMarkPut: sdk.mark,
  listProjectJobsApiV1ProjectsProjectIdJobsGet: sdk.jobs,
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
      focused: false,
      figure: 'default',
      active: false,
      steps: [],
      choose: vi.fn(),
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

  async function render(
    item: StripItem,
    editor: EditorSession | null = null,
    state = processing(),
  ): Promise<void> {
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <ThisPageSection
            processing={state}
            items={[item]}
            item={item}
            selected={new Set()}
            editor={editor}
          />
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
    sdk.remake.mockReset();
    sdk.mark.mockReset();
    sdk.jobs.mockReset();
    sdk.versions.mockResolvedValue({
      data: { items: [OLD, NEW], total: 2, page: 1, size: 100, pages: 1 },
    });
    sdk.choose.mockResolvedValue({ data: {} });
    sdk.remake.mockResolvedValue({ data: { id: 'job' } });
    sdk.mark.mockResolvedValue({ data: {} });
    sdk.jobs.mockResolvedValue({ data: { items: [], total: 0, page: 1, size: 100, pages: 1 } });
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

  it('writes how bent the lines of the page were and how many of them the dewarping followed', async () => {
    const dewarped = version('dewarped', { data: { bend: 12.34, lines: 24, confidence: 0.97 } });
    await render({ page: page('page'), row: row('page', { version: dewarped }) });

    expect(text('this-page-facts')).toContain('Bend of the lines12.3 px per 1000 px of width');
    expect(text('this-page-facts')).toContain('Lines followed24');
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

  it('marks a result whose picture was removed, and makes it again instead of choosing it', async () => {
    const removed = version('old', {
      created_at: '2026-10-01T10:00:00Z',
      files_removed: true,
      files_removed_at: '2026-10-02T00:00:00Z',
      images: null,
    });
    sdk.versions.mockResolvedValue({
      data: { items: [removed, NEW], total: 2, page: 1, size: 100, pages: 1 },
    });
    await render({ page: page('page'), row: row('page', { version: NEW }) });

    expect(text('history-removed')).toBe('Picture removed · made again on use');
    await act(async () => {
      container.querySelector<HTMLElement>('[data-testid="history-use"]')?.click();
    });

    expect(sdk.choose).not.toHaveBeenCalled();
    expect(sdk.remake).toHaveBeenCalledTimes(1);
    expect(sdk.remake.mock.calls[0]?.[0]).toMatchObject({
      path: { project_id: 'project', page_id: 'page', version_id: 'old' },
    });
  });

  it('says nothing about a picture on a result that has one', async () => {
    await render({ page: page('page'), row: row('page', { version: NEW }) });

    expect(container.querySelector('[data-testid="history-removed"]')).toBeNull();
  });

  it('offers no button on the current result', async () => {
    await render({ page: page('page'), row: row('page', { version: NEW }) });

    const current = container.querySelector('[data-testid="history-entry"][data-current="true"]');
    expect(current?.querySelector('[data-testid="history-use"]')).toBeNull();
    expect(current?.textContent).toContain('Current');
  });

  describe('the mark and the comment of a result', () => {
    const entry = (id: string): HTMLElement | null =>
      container.querySelector<HTMLElement>(
        `[data-testid="history-entry"]:has([data-testid="result-mark-good"][data-version="${id}"])`,
      );
    const pressed = (id: string, mark: 'good' | 'bad'): string | null | undefined =>
      container
        .querySelector(`[data-version="${id}"][data-testid="result-mark-${mark}"]`)
        ?.getAttribute('aria-pressed');
    const click = async (id: string, testId: string): Promise<void> => {
      await act(async () => {
        container
          .querySelector<HTMLElement>(`[data-version="${id}"][data-testid="${testId}"]`)
          ?.click();
      });
    };

    it('offers a mark and a comment on every result, the current one included', async () => {
      await render({ page: page('page'), row: row('page', { version: NEW }) });

      expect(container.querySelectorAll('[data-testid="result-note"]')).toHaveLength(2);
      expect(entry('new')).not.toBeNull();
    });

    it('marks a result good, which sends the mark with the comment the result has', async () => {
      sdk.versions.mockResolvedValue({
        data: {
          items: [version('old', { comment: 'Too tight' }), NEW],
          total: 2,
          page: 1,
          size: 100,
          pages: 1,
        },
      });
      await render({ page: page('page'), row: row('page', { version: NEW }) });

      await click('old', 'result-mark-good');

      expect(sdk.mark).toHaveBeenCalledTimes(1);
      expect(sdk.mark.mock.calls[0]?.[0]).toMatchObject({
        path: { project_id: 'project', page_id: 'page', version_id: 'old' },
        body: { mark: 'good', comment: 'Too tight' },
      });
    });

    it('shows the mark a result has as pressed, and takes it off by pressing it again', async () => {
      sdk.versions.mockResolvedValue({
        data: {
          items: [version('old', { mark: 'bad' }), NEW],
          total: 2,
          page: 1,
          size: 100,
          pages: 1,
        },
      });
      await render({ page: page('page'), row: row('page', { version: NEW }) });

      expect([pressed('old', 'bad'), pressed('old', 'good'), pressed('new', 'bad')]).toEqual([
        'true',
        'false',
        'false',
      ]);
      await click('old', 'result-mark-bad');

      expect(sdk.mark.mock.calls[0]?.[0]).toMatchObject({ body: { mark: null, comment: '' } });
    });

    it('writes a comment of several lines and keeps the mark', async () => {
      sdk.versions.mockResolvedValue({
        data: {
          items: [version('old', { mark: 'good' }), NEW],
          total: 2,
          page: 1,
          size: 100,
          pages: 1,
        },
      });
      await render({ page: page('page'), row: row('page', { version: NEW }) });

      await click('old', 'result-comment-edit');
      const input = container.querySelector<HTMLTextAreaElement>(
        '[data-testid="result-comment-input"]',
      );
      await act(async () => {
        const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
        setter?.call(input, 'First try\nSecond try');
        input?.dispatchEvent(new Event('input', { bubbles: true }));
      });
      await act(async () => {
        container.querySelector<HTMLElement>('[data-testid="result-comment-save"]')?.click();
      });

      expect(sdk.mark.mock.calls[0]?.[0]).toMatchObject({
        path: { version_id: 'old' },
        body: { mark: 'good', comment: 'First try\nSecond try' },
      });
    });

    it('shows the comment a result has, and offers to add one to a result without', async () => {
      sdk.versions.mockResolvedValue({
        data: {
          items: [version('old', { comment: 'Too tight' }), NEW],
          total: 2,
          page: 1,
          size: 100,
          pages: 1,
        },
      });
      await render({ page: page('page'), row: row('page', { version: NEW }) });

      const notes = [...container.querySelectorAll('[data-testid="result-note"]')].map(
        (note) => note.textContent,
      );
      expect(notes[0]).toContain('Too tight');
      expect(notes[0]).toContain('Edit comment');
      expect(notes[1]).toContain('Add a comment');
    });

    it('tells the reader when the notes could not be saved', async () => {
      sdk.mark.mockRejectedValue(
        new ProblemError('Another job of this book is running.', 409, null, []),
      );
      await render({ page: page('page'), row: row('page', { version: NEW }) });

      await click('old', 'result-mark-good');

      expect(container.querySelector('[role="alert"]')).not.toBeNull();
    });
  });

  describe('a recipe of two steps', () => {
    const first = version('first', {
      created_at: '2026-10-01T10:00:00Z',
      data: { angle: 0.5, confidence: 0.8 },
    });
    const second = version('second', {
      created_at: '2026-10-01T10:01:00Z',
      input_id: 'first',
      processor: { key: 'geometry.crop', version: '1' },
      data: { angle: 2, confidence: 0.9 },
    });
    const two = recipe('r1', { steps: [step('geometry.deskew'), step('geometry.crop')] });
    const state = (
      overrides: Parameters<typeof processing>[0] = {},
    ): ReturnType<typeof processing> => processing({ recipe: two, recipes: [two], ...overrides });

    beforeEach(() => {
      sdk.versions.mockResolvedValue({
        data: { items: [first, second], total: 2, page: 1, size: 100, pages: 1 },
      });
    });

    it('lets the reader choose the step whose result is shown, and names the steps that are on', async () => {
      const showStep = vi.fn();
      await render(
        { page: page('page'), row: row('page', { version: second, recipe_id: 'r1' }) },
        null,
        state({ showStep }),
      );
      const select = container.querySelector<HTMLSelectElement>('[data-testid="this-page-step"]');

      expect([...(select?.options ?? [])].map((option) => option.textContent)).toEqual([
        'Last step',
        '1 · Deskew',
        '2 · geometry.crop',
      ]);
      await act(async () => {
        if (select !== null) {
          select.value = '0';
          select.dispatchEvent(new Event('change', { bubbles: true }));
        }
      });
      expect(showStep).toHaveBeenCalledWith(0);
    });

    it('reads what the first step found when it is the step chosen', async () => {
      await render(
        { page: page('page'), row: row('page', { version: second, recipe_id: 'r1' }) },
        null,
        state({ shownStep: 0 }),
      );

      expect(text('this-page-facts')).toContain('Turned by0.5°');
    });

    it('says a page stopped at a step is not read by the next stage until the rest is run', async () => {
      await render(
        {
          page: page('page'),
          row: row('page', { version: first, recipe_id: 'r1', through_step: 0 }),
        },
        null,
        state(),
      );

      expect(text('this-page-stopped')).toContain('Run through step 1 of 2 only.');
    });

    it('says when the page has not reached the step chosen', async () => {
      await render(
        {
          page: page('page'),
          row: row('page', { version: first, recipe_id: 'r1', through_step: 0 }),
        },
        null,
        state({ shownStep: 1 }),
      );

      expect(text('this-page-not-reached')).toContain('has not reached step 2');
    });

    it('offers no choice of step for a recipe of one', async () => {
      await render({ page: page('page'), row: row('page', { version: NEW }) });

      expect(container.querySelector('[data-testid="this-page-step"]')).toBeNull();
    });
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
