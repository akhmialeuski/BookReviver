import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { JobSchema, PageVersionSchema } from '@/api';
import { version } from '@/features/processing/fixtures';
import type { PreviewRequest } from '@/features/processing/preview';
import { BUSY_RETRY_MS, type PreviewResult, usePreview } from '@/features/processing/usePreview';
import { applyProjectEvent, EventName } from '@/features/projects/events';
import { ProblemError } from '@/shared/http/problem';

/**
 * The preview of the form: it asks once the form has stood still for 400 ms, shows the version the server announces,
 * asks nothing again for a form it has shown, and waits when another job of the book is going.
 *
 * The generated client is replaced by functions the test answers, and the event stream by calls of `applyProjectEvent`,
 * which is what the stream does with an event.
 */

const sdk = vi.hoisted(() => ({
  preview: vi.fn(),
  job: vi.fn(),
  version: vi.fn(),
  jobs: vi.fn(),
  versions: vi.fn(),
}));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  previewStepApiV1ProjectsProjectIdStagesStagePreviewPost: sdk.preview,
  readJobApiV1JobsJobIdGet: sdk.job,
  getVersionApiV1ProjectsProjectIdPagesPageIdVersionsVersionIdGet: sdk.version,
  listProjectJobsApiV1ProjectsProjectIdJobsGet: sdk.jobs,
  listVersionsApiV1ProjectsProjectIdPagesPageIdVersionsGet: sdk.versions,
}));

const PROJECT = 'book';
const PAGE = 'page';

function ask(angle: number, pageId = PAGE): PreviewRequest {
  return {
    pageId,
    stepIndex: 0,
    steps: [
      { processor_key: 'geometry.deskew', params: { max_angle: angle, min_confidence: 0.3 } },
    ],
  };
}

function made(id: string, angle: number): PageVersionSchema {
  return version(id, {
    scale: 'preview',
    preview: `/previews/${id}.png`,
    params: { max_angle: angle, min_confidence: 0.3 },
  });
}

function jobOf(id: string, state: JobSchema['state'] = 'running'): JobSchema {
  return {
    id,
    project_id: PROJECT,
    kind: 'preview-step',
    stage: null,
    state,
    progress: { done: 0, total: 1, fraction: 0 },
    error: '',
    result: null,
    created_at: '2026-10-01T00:00:00Z',
    started_at: null,
    finished_at: null,
  };
}

describe('usePreview', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;
  let latest: PreviewResult;

  function Probe({ request, on }: { request: PreviewRequest | null; on: boolean }): null {
    latest = usePreview(PROJECT, 'geometry', request, on);
    return null;
  }

  function render(request: PreviewRequest | null, on = true): void {
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <Probe request={request} on={on} />
        </QueryClientProvider>,
      ),
    );
  }

  /** Let the timers run and the answers of the fake server be read. */
  async function elapse(ms: number): Promise<void> {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  }

  /** Say what the stream says when the server has made a preview. */
  async function announce(id: string): Promise<void> {
    act(() =>
      applyProjectEvent(client, PROJECT, {
        event: EventName.PageVersionReady,
        data: { project_id: PROJECT, page_id: PAGE, version_id: id },
      }),
    );
    await elapse(0);
  }

  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    for (const fake of Object.values(sdk)) {
      fake.mockReset();
    }
    sdk.jobs.mockResolvedValue({ data: { items: [], total: 0, page: 1, size: 20, pages: 1 } });
    sdk.job.mockImplementation(async ({ path }) => ({ data: jobOf(path.job_id) }));
    let counter = 0;
    sdk.preview.mockImplementation(async () => {
      counter += 1;
      return { data: jobOf(`job-${counter}`) };
    });
    sdk.version.mockImplementation(async ({ path }) => ({ data: made(path.version_id, 5) }));
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    client.clear();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('asks nothing while the preview is off', async () => {
    render(ask(5), false);

    await elapse(2_000);

    expect(sdk.preview).not.toHaveBeenCalled();
  });

  it('asks nothing for a form that has not stood still for 400 ms', async () => {
    render(ask(5));

    await elapse(399);

    expect(sdk.preview).not.toHaveBeenCalled();
  });

  it('asks once for a form changed again and again within 400 ms, with the last one', async () => {
    render(ask(5));
    await elapse(300);
    render(ask(6));
    await elapse(300);
    render(ask(7));
    await elapse(400);

    expect(sdk.preview).toHaveBeenCalledTimes(1);
    expect(sdk.preview.mock.calls[0]?.[0].body).toEqual({
      page_id: PAGE,
      steps: [{ processor_key: 'geometry.deskew', params: { max_angle: 7, min_confidence: 0.3 } }],
      step_index: 0,
    });
  });

  it('says the preview is being made until the server announces it, and then shows it', async () => {
    render(ask(5));
    await elapse(400);
    expect(latest.working).toBe(true);
    expect(latest.shown).toBeNull();

    sdk.version.mockResolvedValue({ data: made('v5', 5) });
    await announce('v5');

    expect(latest.shown?.id).toBe('v5');
    expect(latest.working).toBe(false);
  });

  it('ignores a version that answers another form', async () => {
    render(ask(5));
    await elapse(400);

    sdk.version.mockResolvedValue({ data: made('other', 9) });
    await announce('other');

    expect(latest.shown).toBeNull();
    expect(latest.working).toBe(true);
  });

  it('asks nothing again for a form it has shown, even after another was shown meanwhile', async () => {
    render(ask(5));
    await elapse(400);
    sdk.version.mockResolvedValue({ data: made('v5', 5) });
    await announce('v5');

    render(ask(6));
    await elapse(400);
    sdk.version.mockResolvedValue({ data: made('v6', 6) });
    await announce('v6');
    expect(sdk.preview).toHaveBeenCalledTimes(2);

    render(ask(5));
    await elapse(2_000);

    expect(sdk.preview).toHaveBeenCalledTimes(2);
    expect(latest.shown?.id).toBe('v5');
    expect(latest.working).toBe(false);
  });

  it('follows the reader to another page', async () => {
    render(ask(5));
    await elapse(400);
    sdk.version.mockResolvedValue({ data: made('v5', 5) });
    await announce('v5');

    render(ask(5, 'next-page'));
    await elapse(400);

    expect(sdk.preview).toHaveBeenCalledTimes(2);
    expect(sdk.preview.mock.calls[1]?.[0].body.page_id).toBe('next-page');
  });

  it('waits for its own job to end before asking again with a newer form', async () => {
    render(ask(5));
    await elapse(400);
    expect(sdk.preview).toHaveBeenCalledTimes(1);

    render(ask(6));
    await elapse(400);

    expect(sdk.preview).toHaveBeenCalledTimes(1);
  });

  it('finds a preview the server answered from its cache, which no event announced', async () => {
    sdk.job.mockImplementation(async ({ path }) => ({ data: jobOf(path.job_id, 'succeeded') }));
    sdk.versions.mockResolvedValue({
      data: { items: [made('cached', 5)], total: 1, page: 1, size: 100, pages: 1 },
    });

    render(ask(5));
    await elapse(400);
    await elapse(0);

    expect(latest.shown?.id).toBe('cached');
  });

  it('says why a job that failed did, and does not ask again for the same form', async () => {
    sdk.job.mockImplementation(async ({ path }) => ({
      data: { ...jobOf(path.job_id, 'failed'), error: 'The page has no image to preview.' },
    }));

    render(ask(5));
    await elapse(400);
    await elapse(5_000);

    expect(latest.error).toBe('The page has no image to preview.');
    expect(sdk.preview).toHaveBeenCalledTimes(1);

    render(ask(6));
    await elapse(400);
    expect(sdk.preview).toHaveBeenCalledTimes(2);
  });

  it('stops waiting for a job that a run cancelled, with no error and no ask again for the same form', async () => {
    sdk.job.mockImplementation(async ({ path }) => ({ data: jobOf(path.job_id, 'cancelled') }));

    render(ask(5));
    await elapse(400);
    await elapse(5_000);

    expect(latest.error).toBeNull();
    expect(latest.working).toBe(false);
    expect(latest.waiting).toBe(false);
    expect(sdk.preview).toHaveBeenCalledTimes(1);

    render(ask(6));
    await elapse(400);
    expect(sdk.preview).toHaveBeenCalledTimes(2);
  });

  it('waits while another job of the book is going, and asks when it has ended', async () => {
    sdk.jobs.mockResolvedValue({
      data: { items: [jobOf('run')], total: 1, page: 1, size: 20, pages: 1 },
    });
    render(ask(5));
    await elapse(2_000);

    expect(sdk.preview).not.toHaveBeenCalled();
    expect(latest.waiting).toBe(true);

    sdk.jobs.mockResolvedValue({ data: { items: [], total: 0, page: 1, size: 20, pages: 1 } });
    await act(async () => {
      await client.invalidateQueries();
    });
    await elapse(0);

    expect(sdk.preview).toHaveBeenCalledTimes(1);
  });

  it('asks again a moment later when the server turned the ask away because of a job it did not list', async () => {
    sdk.preview.mockRejectedValueOnce(new ProblemError('Another job is running.', 409, null, []));

    render(ask(5));
    await elapse(400);
    expect(sdk.preview).toHaveBeenCalledTimes(1);

    await elapse(BUSY_RETRY_MS - 1);
    expect(sdk.preview).toHaveBeenCalledTimes(1);
    await elapse(1);
    expect(sdk.preview).toHaveBeenCalledTimes(2);
  });
});
