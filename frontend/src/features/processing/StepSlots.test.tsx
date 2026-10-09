import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  deskew,
  pageValues,
  processing,
  processor,
  stepSettings,
} from '@/features/processing/fixtures';
import type { OrderIssue } from '@/features/processing/order';
import type { PageValues } from '@/features/processing/pageSettings';
import type { StepDraft } from '@/features/processing/recipe';
import { StepCarry, StepNotes, StepSettings } from '@/features/processing/StepSlots';

const sdk = vi.hoisted(() => ({
  carry: vi.fn(),
  history: vi.fn(),
  versions: vi.fn(),
}));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  carryOverEditApiV1ProjectsProjectIdPagesPageIdEditsStageStepIdCarryOverPost: sdk.carry,
  listHistoryApiV1ProjectsProjectIdPagesPageIdHistoryStageStepIdGet: sdk.history,
  listVersionsApiV1ProjectsProjectIdPagesPageIdVersionsGet: sdk.versions,
}));

/**
 * What a processing stage puts in the slots of its panel for the open step: the notes under the title of the step, the
 * form of its settings with the values pages have for each, the measurement of the book, and the carry-over of a shape set
 * by hand.
 *
 * Radix measures the thumb of a slider, which jsdom cannot, so the observer it asks for is given a stand-in.
 */

class SizeObserverStandIn {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

const CATALOGUE = [processor('geometry.perspective', { title: 'Perspective' }), deskew()];

function draftOf(processorKey: string, overrides: Partial<StepDraft> = {}): StepDraft {
  return {
    id: 'step-1',
    stepId: 'b',
    processorKey,
    params: { max_angle: 5, min_confidence: 0.3 },
    enabled: true,
    ...overrides,
  };
}

describe('the slots of the open step', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;
  const change = vi.fn();
  const restoreOrder = vi.fn();

  function render(element: React.JSX.Element): void {
    act(() => root.render(<QueryClientProvider client={client}>{element}</QueryClientProvider>));
  }

  const find = (testId: string): HTMLElement | null =>
    container.querySelector<HTMLElement>(`[data-testid="${testId}"]`);
  const text = (testId: string): string => find(testId)?.textContent ?? '';

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    vi.stubGlobal('ResizeObserver', SizeObserverStandIn);
    for (const mock of [change, restoreOrder, sdk.carry, sdk.history, sdk.versions]) {
      mock.mockReset();
    }
    sdk.carry.mockResolvedValue({ data: { batch_id: 'batch', changes: [], skipped: [] } });
    sdk.history.mockResolvedValue({ data: { items: [], total: 0, page: 1, size: 100, pages: 1 } });
    sdk.versions.mockResolvedValue({ data: { items: [], total: 0, page: 1, size: 100, pages: 1 } });
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    for (const node of document.body.querySelectorAll('[role="menu"]')) {
      node.remove();
    }
    client.clear();
    vi.unstubAllGlobals();
  });

  describe('the notes under the title of the step', () => {
    const REASON =
      'Deskew reads the slant of the lines on an upright sheet, so it usually comes after Perspective.';
    const REQUIRED_REASON = 'Margins cannot come before Select content.';
    const issue = (kind: 'usual' | 'required', reason: string): OrderIssue => ({
      stepId: 'step-1',
      otherId: 'step-0',
      kind,
      reason,
    });

    function renderNotes(issues: readonly OrderIssue[], enabled = true): void {
      const draft = draftOf('geometry.deskew');
      render(
        <StepNotes
          processing={processing({
            catalogue: CATALOGUE,
            orderIssues: new Map([[draft.id, issues]]),
            restoreOrder,
          })}
          draft={draft}
          enabled={enabled}
        />,
      );
    }

    it('gives every reason the step stands off its place, with the button that restores the usual order', () => {
      renderNotes([issue('usual', REASON), issue('required', REQUIRED_REASON)]);

      expect(text('step-order-details')).toContain(REASON);
      expect(text('step-order-details')).toContain(REQUIRED_REASON);
      expect(find('step-order-details')?.dataset.kind).toBe('required');
      act(() => find('step-restore-order')?.click());
      expect(restoreOrder).toHaveBeenCalledTimes(1);
    });

    it('draws nothing for a step that stands in its place and is on', () => {
      renderNotes([]);

      expect(container.innerHTML).toBe('');
    });

    it('says a step that is off is skipped by a run and a preview', () => {
      renderNotes([], false);

      expect(container.textContent).toContain('This step is off');
      expect(find('step-order-details')).toBeNull();
    });
  });

  describe('the settings of the step', () => {
    const VALUES = pageValues({
      settings: [
        stepSettings('b', {
          params: { min_confidence: 0.6 },
          parts: [
            {
              scope: 'even',
              group_label: '',
              params: { max_angle: 3 },
              updated_at: '2026-10-01T00:00:00Z',
            },
          ],
        }),
      ],
    });

    function renderSettings(
      processorKey = 'geometry.deskew',
      values: PageValues | undefined = undefined,
    ): void {
      const found = CATALOGUE.find((entry) => entry.key === processorKey) ?? deskew();
      render(
        <StepSettings
          processing={processing({ catalogue: CATALOGUE, change })}
          draft={draftOf(processorKey)}
          processor={found}
          values={values}
        />,
      );
    }

    it('draws the form with ids of its own, so the form of the page can stand beside it', () => {
      renderSettings();

      expect(container.querySelector('[id^="step-panel_"]')).not.toBeNull();
      expect(container.querySelector('[id^="root_"]')).toBeNull();
    });

    it('draws the values of each setting under it, and nothing else of the page', () => {
      renderSettings('geometry.deskew', VALUES);

      const chips = [...container.querySelectorAll('[data-testid="value-chip"]')].map(
        (chip) => chip.textContent,
      );
      expect(chips).toEqual(['Even pages 3', 'p. 143 0.6']);
      for (const gone of [
        'page-settings',
        'page-settings-edit',
        'reset-menu',
        'step-panel-book',
        'step-passed',
        'step-count-found',
      ]) {
        expect(find(gone)).toBeNull();
      }
      expect(sdk.history).not.toHaveBeenCalled();
    });

    it('draws no values when no page is open', () => {
      renderSettings();

      expect(find('field-values')).toBeNull();
    });

    it('draws no heading of its own, since the frame of the layout holds it', () => {
      renderSettings();

      expect(container.querySelector('h3')).toBeNull();
      expect(container.textContent).not.toContain('Settings of the step');
    });

    it('stands the measurement of the book in the settings of the step that places the block, and in no other', () => {
      renderSettings('geometry.normalize');
      expect(find('measure-book')).not.toBeNull();

      renderSettings();
      expect(find('measure-book')).toBeNull();
    });
  });

  describe('carrying the shape over', () => {
    function renderCarry(selected: ReadonlySet<string> = new Set()): void {
      render(
        <StepCarry
          processing={processing({ catalogue: CATALOGUE })}
          pageId="p1"
          stepId="b"
          selected={selected}
          result={null}
          onResult={vi.fn()}
        />,
      );
    }

    async function choose(id: string): Promise<void> {
      const trigger = find('carry-menu');
      await act(async () => {
        trigger?.focus();
        trigger?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
      });
      await act(async () => {
        document.body.querySelector<HTMLElement>(`[data-testid="${id}"]`)?.click();
      });
    }

    it('carries the shape of the step to the following pages, naming no field', async () => {
      renderCarry();
      await choose('carry-following');

      expect(sdk.carry).toHaveBeenCalledTimes(1);
      expect(sdk.carry.mock.calls[0]?.[0]).toMatchObject({
        path: { page_id: 'p1', stage: 'geometry', step_id: 'b' },
        body: { scope: 'following', overwrite: false },
      });
    });

    it('carries it to every page of the same kind, over the pages with a shape of their own when asked', async () => {
      renderCarry();
      act(() => find('step-carry-overwrite')?.click());
      await choose('carry-kind');

      expect(sdk.carry.mock.calls[0]?.[0]).toMatchObject({
        body: { scope: 'kind', overwrite: true },
      });
    });

    it('carries it to the pages selected in the grid, but the open one', async () => {
      renderCarry(new Set(['p1', 'p2']));
      await choose('carry-selected');

      expect(sdk.carry.mock.calls[0]?.[0]).toMatchObject({
        body: { scope: 'selected', page_ids: ['p2'] },
      });
    });
  });
});
