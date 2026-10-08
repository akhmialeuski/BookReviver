import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useReducer, useRef, useState } from 'react';
import {
  listVersionsApiV1ProjectsProjectIdPagesPageIdVersionsGet,
  type PageVersionSchema,
  type Stage,
} from '@/api';
import {
  getVersionApiV1ProjectsProjectIdPagesPageIdVersionsVersionIdGetOptions,
  readJobApiV1JobsJobIdGetOptions,
} from '@/api/@tanstack/react-query.gen';
import {
  answersPreview,
  PREVIEW_DELAY_MS,
  type PreviewRequest,
  previewKey,
} from '@/features/processing/preview';
import { usePreviewStep } from '@/features/processing/queries';
import { isActiveJob } from '@/features/projects/events';
import { type VersionReady, versionReadyKey } from '@/features/projects/queries';
import { useActiveJobs } from '@/features/workspace/queries';
import { useDebouncedCallback } from '@/shared/hooks/useDebouncedCallback';
import { describeError, ProblemError } from '@/shared/http/problem';
import { HttpStatus } from '@/shared/http/status';

/**
 * The preview of the steps of the form on the open page.
 *
 * While the preview is on, every change of the form, and every turn to another page, asks the server for a preview
 * once the change has stood for 400 ms, so a slider dragged across its range asks once. The server makes the preview
 * in the background and announces the version it made with the `page-version-ready` event, which `events.ts` leaves in
 * the cache; the version is read and shown if it answers the ask. An ask for what is shown already starts nothing.
 *
 * A project runs one job at a time, so an ask that is made while another is under way waits for it, and one that
 * changes while its predecessor is still being made is made when that has ended, with the newest form. A job that
 * ends without an event, as one answered from the cache of the server does, is looked up among the previews of the page.
 */

/** The page size of the list of previews that is searched when the event did not come. */
const SEARCH_SIZE = 100;

/** How long an ask that was turned away waits after the other job has ended before it is made again. */
export const BUSY_RETRY_MS = 1000;

interface Inflight {
  key: string;
  request: PreviewRequest;
  jobId: string;
}

interface Shown {
  key: string;
  version: PageVersionSchema;
}

interface State {
  shown: Shown | null;
  /** The previews already made in this session by the key of their ask, so an ask that comes again starts no work. */
  known: Readonly<Record<string, PageVersionSchema>>;
  inflight: Inflight | null;
  /** The key of the ask that failed or was cancelled, which is not asked again until the form changes. */
  failedKey: string | null;
  error: string | null;
  /** Whether the server turned the last ask away because another job of the book is going. */
  busy: boolean;
}

type Action =
  | { type: 'sent'; inflight: Inflight }
  | { type: 'resolved'; shown: Shown }
  | { type: 'recalled'; shown: Shown }
  | { type: 'failed'; key: string; error: string }
  | { type: 'cancelled'; key: string }
  | { type: 'busy' }
  | { type: 'idle' };

const INITIAL: State = {
  shown: null,
  known: {},
  inflight: null,
  failedKey: null,
  error: null,
  busy: false,
};

function reduce(state: State, action: Action): State {
  switch (action.type) {
    case 'sent':
      return { ...state, inflight: action.inflight, error: null, busy: false };
    case 'resolved':
      return {
        ...INITIAL,
        known: { ...state.known, [action.shown.key]: action.shown.version },
        shown: action.shown,
      };
    case 'recalled':
      return { ...state, shown: action.shown, failedKey: null, error: null };
    case 'failed':
      return { ...state, inflight: null, failedKey: action.key, error: action.error, busy: false };
    case 'cancelled':
      return { ...state, inflight: null, failedKey: action.key, error: null, busy: false };
    case 'busy':
      return { ...state, busy: true };
    case 'idle':
      return { ...state, busy: false };
  }
}

/**
 * Hold back an ask until it has stood still for a while.
 *
 * The screen builds a new object for the same ask on every render, so the wait is on the key, and the ask that is
 * given back is the newest one when the key has stood still.
 */
function useSettled(request: PreviewRequest | null, key: string | null): PreviewRequest | null {
  // Nothing has stood still yet when the screen is first drawn
  const [settled, setSettled] = useState<PreviewRequest | null>(null);
  const settle = useDebouncedCallback(
    (settledKey: string | null) => setSettled(settledKey === null ? null : request),
    PREVIEW_DELAY_MS,
    { flushOnUnmount: false },
  );
  useEffect(() => {
    settle(key);
  }, [key, settle]);
  return settled;
}

/** What the preview offers the screen. */
export interface PreviewResult {
  /** The preview the server made for the page and the form as they are now, or the last one it made. */
  shown: PageVersionSchema | null;
  /** Whether an ask is waiting for its turn or being made, so the picture on show is not up to date. */
  working: boolean;
  /** Whether the ask waits for another job of the book to end. */
  waiting: boolean;
  error: string | null;
}

/**
 * Keep a preview of a request up to date while it is on.
 *
 * @param projectId The book.
 * @param stage The stage of the steps.
 * @param request The steps of the form and the page, or null when there is nothing to preview.
 * @param on Whether the reader asked for the preview.
 */
export function usePreview(
  projectId: string,
  stage: Stage,
  request: PreviewRequest | null,
  on: boolean,
): PreviewResult {
  const queryClient = useQueryClient();
  const [state, dispatch] = useReducer(reduce, INITIAL);
  const { mutateAsync: ask } = usePreviewStep(projectId);
  const activeJobs = useActiveJobs(projectId);
  const key = request === null ? null : previewKey(request);
  // Turning the preview on is a change like any other, so the first ask waits for the form to stand still as well
  const settled = useSettled(on ? request : null, on ? key : null);
  const settledKey = settled === null ? null : previewKey(settled);
  const anotherJobGoing = (activeJobs.data?.length ?? 0) > 0;

  // Ask for the preview once the form has settled, when nothing else is being asked
  const { inflight, shown, known, failedKey, busy } = state;
  const asking = useRef<string | null>(null);
  useEffect(() => {
    if (
      !on ||
      settled === null ||
      settledKey === null ||
      inflight !== null ||
      settledKey === shown?.key ||
      settledKey === failedKey ||
      asking.current === settledKey ||
      busy ||
      anotherJobGoing
    ) {
      return;
    }
    const recalled = known[settledKey];
    if (recalled !== undefined) {
      dispatch({ type: 'recalled', shown: { key: settledKey, version: recalled } });
      return;
    }
    // The answer of this ask is taken whatever the screen does meanwhile, and the same ask is not made twice
    asking.current = settledKey;
    ask({
      path: { project_id: projectId, stage },
      body: { page_id: settled.pageId, steps: [...settled.steps], step_index: settled.stepIndex },
    })
      .then((job) =>
        dispatch({ type: 'sent', inflight: { key: settledKey, request: settled, jobId: job.id } }),
      )
      .catch((error: unknown) =>
        error instanceof ProblemError && error.status === HttpStatus.Conflict
          ? dispatch({ type: 'busy' })
          : dispatch({ type: 'failed', key: settledKey, error: describeError(error) }),
      )
      .finally(() => {
        asking.current = null;
      });
  }, [
    on,
    settled,
    settledKey,
    inflight,
    shown?.key,
    known,
    failedKey,
    busy,
    anotherJobGoing,
    ask,
    projectId,
    stage,
  ]);

  // The ask that was turned away goes out again a moment after the job that stood in its way has ended. A job the book
  // does not list yet is waited for the same moment, so a server that keeps turning the ask away is not asked in a loop
  useEffect(() => {
    if (!busy || anotherJobGoing) {
      return;
    }
    const timer = setTimeout(() => dispatch({ type: 'idle' }), BUSY_RETRY_MS);
    return () => clearTimeout(timer);
  }, [busy, anotherJobGoing]);

  // The server announces the version it made
  const ready = useQuery<VersionReady | null>({
    queryKey: versionReadyKey(projectId, inflight?.request.pageId ?? ''),
    queryFn: () => null,
    enabled: false,
    staleTime: Number.POSITIVE_INFINITY,
  });
  const readyAt = ready.data?.at;
  const readyId = ready.data?.versionId;
  useEffect(() => {
    if (inflight === null || readyId === undefined || readyAt === undefined) {
      return;
    }
    let current = true;
    void queryClient
      .fetchQuery(
        getVersionApiV1ProjectsProjectIdPagesPageIdVersionsVersionIdGetOptions({
          path: { project_id: projectId, page_id: inflight.request.pageId, version_id: readyId },
        }),
      )
      .then((version) => {
        if (current && answersPreview(version, inflight.request)) {
          dispatch({ type: 'resolved', shown: { key: inflight.key, version } });
        }
      })
      .catch(() => undefined);
    return () => {
      current = false;
    };
  }, [inflight, readyAt, readyId, projectId, queryClient]);

  // The job ends, which is the end of the ask even when no event named a version
  const job = useQuery({
    ...readJobApiV1JobsJobIdGetOptions({ path: { job_id: inflight?.jobId ?? '' } }),
    enabled: inflight !== null,
    refetchInterval: (query) => (isActiveJob(query.state.data) ? 1500 : false),
  });
  const jobState = job.data?.state;
  const jobError = job.data?.error ?? '';
  useEffect(() => {
    if (inflight === null) {
      return;
    }
    // A run or a measure of the book takes the project from a preview by cancelling it, which is no failure of the preview,
    // so the hook stops waiting for it and says nothing
    if (jobState === 'cancelled') {
      dispatch({ type: 'cancelled', key: inflight.key });
      return;
    }
    if (jobState === 'failed') {
      dispatch({ type: 'failed', key: inflight.key, error: jobError });
      return;
    }
    if (jobState !== 'succeeded') {
      return;
    }
    let current = true;
    void findPreview(projectId, stage, inflight.request).then((version) => {
      if (!current) {
        return;
      }
      if (version === null) {
        dispatch({ type: 'failed', key: inflight.key, error: '' });
      } else {
        dispatch({ type: 'resolved', shown: { key: inflight.key, version } });
      }
    });
    return () => {
      current = false;
    };
  }, [inflight, jobState, jobError, projectId, stage]);

  const matches = on && key !== null && state.shown?.key === key;
  return {
    shown: state.shown?.version ?? null,
    // An ask that was given up, whether it failed or was cancelled, is no longer being made
    working: on && key !== null && !matches && state.failedKey !== key,
    waiting: on && key !== null && !matches && inflight === null && (busy || anotherJobGoing),
    error: on && state.error !== null && state.failedKey === settledKey ? state.error : null,
  };
}

/**
 * Look for the preview of an ask among the previews of its page, the newest first.
 *
 * The server lists the versions of a page earliest first, so the last page of the list is read first and the search goes
 * backwards from there.
 *
 * @returns The version that answers the ask, or null when there is none.
 */
async function findPreview(
  projectId: string,
  stage: Stage,
  request: PreviewRequest,
): Promise<PageVersionSchema | null> {
  const read = async (page: number) =>
    (
      await listVersionsApiV1ProjectsProjectIdPagesPageIdVersionsGet({
        path: { project_id: projectId, page_id: request.pageId },
        query: { stage, scale: 'preview', page, size: SEARCH_SIZE },
        throwOnError: true,
      })
    ).data;
  const first = await read(1);
  for (let page = first.pages; page >= 1; page -= 1) {
    const { items } = page === 1 ? first : await read(page);
    const found = items.findLast((version) => answersPreview(version, request));
    if (found !== undefined) {
      return found;
    }
  }
  return null;
}
