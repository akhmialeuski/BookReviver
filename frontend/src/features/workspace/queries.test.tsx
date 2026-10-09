import { QueryClient, QueryClientProvider, type UseQueryResult } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { StagePageSchema } from '@/api';
import { invalidateStageRows } from '@/features/projects/queries';
import { row, stepPage } from '@/features/workspace/fixtures';
import { useStepRows } from '@/features/workspace/queries';

/**
 * The rows of a step: a step that opens again shows no rows until the server answers, since the rows it had when it was
 * closed were made stale by the runs since, and nothing read them again.
 */

const sdk = vi.hoisted(() => ({ rows: vi.fn() }));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  listStagePagesApiV1ProjectsProjectIdStagesStagePagesGet: sdk.rows,
}));

const MARGINS = 'margins';
const CROP = 'crop';

/** One page of the rows of a step, as the server answers it. */
function answer(item: StagePageSchema): { data: { items: StagePageSchema[]; pages: number } } {
  return { data: { items: [item], pages: 1 } };
}

describe('useStepRows', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;
  let seen: UseQueryResult<StagePageSchema[]> | undefined;

  function Probe({ step }: { step: string }): null {
    seen = useStepRows('project', 'geometry', step);
    return null;
  }

  async function open(step: string): Promise<void> {
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <Probe step={step} />
        </QueryClientProvider>,
      );
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    sdk.rows.mockReset();
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

  it('shows no rows of a step that opens again until the server answers, and then the rows it answered', async () => {
    const before = row('p1', { step: stepPage(MARGINS, 'default') });
    const after = row('p1', { step: stepPage(MARGINS, 'found') });
    sdk.rows.mockResolvedValue(answer(before));
    await open(MARGINS);
    expect(seen?.data).toEqual([before]);

    // The reader moves to another step, and a run there changes the rows of Margins, which nobody reads again
    sdk.rows.mockResolvedValue(answer(row('p1', { step: stepPage(CROP, 'found') })));
    await open(CROP);
    await act(async () => invalidateStageRows(client, 'project', 'geometry'));

    let respond: (value: ReturnType<typeof answer>) => void = () => undefined;
    sdk.rows.mockReturnValue(
      new Promise((resolve) => {
        respond = resolve;
      }),
    );
    await open(MARGINS);
    expect(seen?.data).toBeUndefined();

    await act(async () => respond(answer(after)));
    await vi.waitFor(() => expect(seen?.data).toEqual([after]));
  });
});
