import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { processing, version } from '@/features/processing/fixtures';
import { ResultsSection } from '@/features/processing/ResultsSection';

/**
 * The results of a stage or of one of its steps on the open page: the list with how each was made, what it found and its
 * settings, the filter by the mark, and the way to make an earlier result the current one.
 *
 * The same section stands for the stage as a whole and for a step of any stage, so it is tested on both.
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

const EMPTY_LIST = { data: { items: [], total: 0, page: 1, size: 100, pages: 1 } };

function listed(...items: ReturnType<typeof version>[]): { data: unknown } {
  return { data: { items, total: items.length, page: 1, size: 100, pages: 1 } };
}

const FIRST = version('first', { created_at: '2026-10-01T10:00:00Z', mark: 'bad' });
const OLD = version('old', {
  created_at: '2026-10-01T11:00:00Z',
  input_id: 'first',
  params: { max_angle: 5, min_confidence: 0.3 },
  mark: 'good',
});
const NEW = version('new', {
  created_at: '2026-10-01T12:00:00Z',
  input_id: 'first',
  params: { max_angle: 9, min_confidence: 0.3 },
  data: { angle: 1.4, confidence: 0.91 },
  edit_hash: '0123456789abcdef',
  origin: 'hand',
  mark: 'bad',
});

describe('ResultsSection', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  async function render(
    props: { stepId?: string | null; currentId?: string; canUse?: boolean } = {},
  ): Promise<void> {
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <ResultsSection
            processing={processing()}
            pageId="page"
            stepId={props.stepId ?? null}
            currentId={props.currentId ?? 'new'}
            canUse={props.canUse}
          />
        </QueryClientProvider>,
      );
    });
    await settled();
  }

  async function settled(): Promise<void> {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  const find = (testId: string): HTMLElement | null =>
    container.querySelector<HTMLElement>(`[data-testid="${testId}"]`);
  const entries = (): string[] =>
    [...container.querySelectorAll('[data-testid="history-entry"]')].map(
      (entry) => entry.getAttribute('data-version') ?? '',
    );
  const choose = async (mark: 'all' | 'good' | 'bad'): Promise<void> => {
    await act(async () => {
      find(`results-filter-${mark}`)?.click();
    });
    await settled();
  };

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    for (const mock of Object.values(sdk)) {
      mock.mockReset();
    }
    sdk.versions.mockResolvedValue(listed(FIRST, OLD, NEW));
    sdk.choose.mockResolvedValue({ data: {} });
    sdk.remake.mockResolvedValue({ data: { id: 'job' } });
    sdk.mark.mockResolvedValue({ data: {} });
    sdk.jobs.mockResolvedValue(EMPTY_LIST);
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

  describe('the notes of a result', () => {
    it('keeps the comment that is being written when the answer to a mark pressed meanwhile arrives', async () => {
      let answer: (value: unknown) => void = () => undefined;
      sdk.mark.mockReturnValue(
        new Promise((resolve) => {
          answer = resolve;
        }),
      );
      await render();
      await act(async () => {
        find('result-comment-edit')?.click();
      });
      expect(find('result-comment-input')).not.toBeNull();
      await act(async () => {
        find('result-mark-good')?.click();
      });

      await act(async () => {
        answer({ data: {} });
      });
      await settled();

      expect(find('result-comment-input')).not.toBeNull();
    });

    it('closes the field of the comment once the comment is saved', async () => {
      sdk.mark.mockResolvedValue({ data: {} });
      await render();
      await act(async () => {
        find('result-comment-edit')?.click();
      });

      await act(async () => {
        find('result-comment-save')?.click();
      });
      await settled();

      expect(sdk.mark.mock.calls[0]?.[0]).toMatchObject({ body: { mark: 'bad', comment: '' } });
      expect(find('result-comment-input')).toBeNull();
    });
  });

  describe('on the stage as a whole', () => {
    it('lists the results of the last step newest first, and leaves out the version a later step read', async () => {
      await render();

      expect(find('results')?.getAttribute('data-scope')).toBe('stage');
      expect(entries()).toEqual(['new', 'old']);
      expect(sdk.versions.mock.calls[0]?.[0].query).toEqual({
        stage: 'geometry',
        scale: 'full',
        size: 100,
        page: 1,
      });
    });

    it('says how each result was made, what it found and with which settings, and tags the current one', async () => {
      await render();

      const [current, earlier] = [...container.querySelectorAll('[data-testid="history-entry"]')];
      expect(current?.getAttribute('data-current')).toBe('true');
      expect(current?.textContent).toContain('Current');
      expect(current?.querySelector('[data-testid="history-origin"]')?.textContent).toBe(
        'Set by hand',
      );
      expect(current?.querySelector('[data-testid="history-settings"]')?.textContent).toBe(
        'Largest slant 9 · Least confidence 0.3',
      );
      expect(current?.querySelector('[data-testid="history-found"]')?.textContent).toBe(
        'Turned by 1.4° · Confidence 0.91 · sure',
      );
      expect(earlier?.querySelector('[data-testid="history-origin"]')?.textContent).toBe(
        'Made by the step',
      );
      expect(earlier?.querySelector('[data-testid="history-found"]')).toBeNull();
    });

    it('narrows the list to the results marked bad, from the list it already has', async () => {
      await render();
      await choose('bad');

      expect(entries()).toEqual(['new']);
      expect(find('results-filter-bad')?.getAttribute('aria-pressed')).toBe('true');
      expect(sdk.versions).toHaveBeenCalledTimes(1);
    });

    it('does not list a version a later step read under the mark it carries, since it is no result of the stage', async () => {
      sdk.versions.mockResolvedValue(listed(FIRST, version('last', { input_id: 'first' })));
      await render({ currentId: 'last' });
      await choose('bad');

      expect(entries()).toEqual([]);
    });

    it('says no result is marked when the list under the filter is empty, and shows all again for All', async () => {
      sdk.versions.mockResolvedValue(listed(FIRST, version('plain', { input_id: 'first' })));
      await render({ currentId: 'plain' });
      await choose('good');

      expect(find('results-empty')?.textContent).toBe('No result of this page is marked good.');
      await choose('all');
      expect(entries()).toEqual(['plain']);
    });

    it('says to run the stage when it has made no result', async () => {
      sdk.versions.mockResolvedValue(EMPTY_LIST);
      await render({ currentId: undefined });

      expect(find('results-empty')?.textContent).toBe('Run the stage to make a result.');
    });

    it('makes an earlier result the current one', async () => {
      await render();

      await act(async () => {
        find('history-use')?.click();
      });

      expect(sdk.choose.mock.calls[0]?.[0]).toMatchObject({
        path: { project_id: 'project', page_id: 'page', stage: 'geometry' },
        body: { version_id: 'old' },
      });
    });

    it('makes the picture of a result again when a collection removed it, instead of choosing it', async () => {
      const removed = version('old', {
        created_at: '2026-10-01T11:00:00Z',
        input_id: 'first',
        files_removed: true,
        files_removed_at: '2026-10-02T00:00:00Z',
        images: null,
      });
      sdk.versions.mockResolvedValue(listed(FIRST, removed, NEW));
      await render();

      expect(find('history-removed')?.textContent).toBe('Picture removed · made again on use');
      await act(async () => {
        find('history-use')?.click();
      });

      expect(sdk.choose).not.toHaveBeenCalled();
      expect(sdk.remake.mock.calls[0]?.[0]).toMatchObject({ path: { version_id: 'old' } });
    });
  });

  describe('on a step', () => {
    it('reads the results of the step from the server, by the step', async () => {
      sdk.versions.mockResolvedValue(listed(OLD, NEW));
      await render({ stepId: 'step-b' });

      expect(find('results')?.getAttribute('data-scope')).toBe('step');
      expect(sdk.versions.mock.calls[0]?.[0].query).toMatchObject({
        stage: 'geometry',
        step: 'step-b',
        scale: 'full',
      });
      expect(sdk.versions.mock.calls[0]?.[0].query.mark).toBeUndefined();
      expect(entries()).toEqual(['new', 'old']);
    });

    it('lists the result of an early step though a later step reads it, since the list holds that step only', async () => {
      sdk.versions.mockResolvedValue(listed(FIRST));
      await render({ stepId: 'step-a', currentId: 'first' });

      expect(entries()).toEqual(['first']);
    });

    it('asks the server for the results marked bad when the filter is set, and lists what it answers', async () => {
      sdk.versions.mockResolvedValue(listed(OLD, NEW));
      await render({ stepId: 'step-b' });
      sdk.versions.mockResolvedValue(listed(NEW));
      await choose('bad');

      expect(sdk.versions.mock.calls.at(-1)?.[0].query).toMatchObject({
        step: 'step-b',
        mark: 'bad',
      });
      expect(entries()).toEqual(['new']);
    });

    it('offers an earlier result as the result of the stage only when it may be one', async () => {
      sdk.versions.mockResolvedValue(listed(OLD, NEW));
      await render({ stepId: 'step-b', canUse: false });
      expect(find('history-use')).toBeNull();
      expect(find('history-entry')?.getAttribute('data-current')).toBe('true');

      await render({ stepId: 'step-b', canUse: true });
      expect(find('history-use')).not.toBeNull();
    });

    it('marks a result of the step, which sends the mark with the comment it has', async () => {
      sdk.versions.mockResolvedValue(listed(OLD, NEW));
      await render({ stepId: 'step-b' });

      await act(async () => {
        find('result-mark-good')?.click();
      });

      expect(sdk.mark.mock.calls[0]?.[0]).toMatchObject({
        path: { project_id: 'project', page_id: 'page', version_id: 'new' },
        body: { mark: 'good', comment: '' },
      });
    });
  });
});
