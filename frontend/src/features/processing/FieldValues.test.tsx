import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { deskew, pageValues, stepSettings } from '@/features/processing/fixtures';
import { ParamsForm } from '@/features/processing/ParamsForm';
import type { PageValues } from '@/features/processing/pageSettings';

/**
 * The values a setting has for parts of the pages, under the setting in the form of its step: a chip for each value, the
 * open page's drawn apart, a cross that takes a value back, and the menu that adds one with the pages each part covers.
 */

const sdk = vi.hoisted(() => ({ put: vi.fn(), remove: vi.fn() }));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  putValueApiV1ProjectsProjectIdStagesStageStepsStepIdValuesNamePut: sdk.put,
  deleteValueApiV1ProjectsProjectIdStagesStageStepsStepIdValuesNameDelete: sdk.remove,
}));

class SizeObserverStandIn {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

const STEP = 'step-1';
const PART = { updated_at: '2026-10-01T00:00:00Z' };

describe('the values of a setting for parts of the pages', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  function render(values: PageValues): void {
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <ParamsForm
            processor={deskew()}
            params={{ max_angle: 5, min_confidence: 0.3 }}
            values={{ page: values, stepId: STEP }}
            onChange={vi.fn()}
          />
        </QueryClientProvider>,
      ),
    );
  }

  const field = (name: string): HTMLElement | null =>
    container.querySelector<HTMLElement>(`[data-testid="field-values"][data-field="${name}"]`);

  async function openMenu(name: string): Promise<void> {
    const trigger = field(name)?.querySelector<HTMLElement>('[data-testid="value-add"]');
    await act(async () => {
      trigger?.focus();
      trigger?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    });
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    vi.stubGlobal('ResizeObserver', SizeObserverStandIn);
    sdk.put.mockReset();
    sdk.remove.mockReset();
    sdk.put.mockResolvedValue({ data: { batch_id: 'b', changes: [] } });
    sdk.remove.mockResolvedValue({ data: { batch_id: 'b', changes: [] } });
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    document.body.querySelectorAll('[role="menu"]').forEach((node) => {
      node.remove();
    });
    client.clear();
    vi.unstubAllGlobals();
  });

  const WITH_VALUES = pageValues({
    selected: ['a', 'b', 'c'],
    sides: { odd: 305, even: 304 },
    groups: [{ label: 'Index', pages: 12 }],
    settings: [
      stepSettings(STEP, {
        params: { max_angle: 4 },
        parts: [
          { scope: 'even', group_label: '', params: { max_angle: 3 }, ...PART },
          { scope: 'group', group_label: 'Index', params: { max_angle: 1 }, ...PART },
        ],
      }),
    ],
  });

  it('draws a chip under the setting for each value, and none under a setting without one', () => {
    render(WITH_VALUES);

    const chips = [...(field('max_angle')?.querySelectorAll('[data-testid="value-chip"]') ?? [])];
    expect(chips.map((chip) => chip.textContent)).toEqual([
      'Even pages 3',
      'Group · Index 1',
      'p. 143 4',
    ]);
    expect(field('min_confidence')?.querySelector('[data-testid="value-chip"]')).toBeNull();
  });

  it('draws the chip of the open page apart from the others', () => {
    render(WITH_VALUES);

    const own = [...container.querySelectorAll('[data-testid="value-chip"]')].map((chip) =>
      chip.getAttribute('data-own'),
    );
    expect(own).toEqual(['false', 'false', 'true']);
  });

  it('takes a value back from the pages it was set for when its cross is pressed', async () => {
    render(WITH_VALUES);
    const crosses = field('max_angle')?.querySelectorAll<HTMLElement>(
      '[data-testid="value-chip-remove"]',
    );

    await act(async () => {
      crosses?.[0]?.click();
    });
    await act(async () => {
      crosses?.[2]?.click();
    });

    expect(sdk.remove.mock.calls[0]?.[0]).toMatchObject({
      path: { project_id: 'project', stage: 'geometry', step_id: STEP, name: 'max_angle' },
      query: { scope: 'even', group_label: '' },
    });
    expect(sdk.remove.mock.calls[1]?.[0]).toMatchObject({
      query: { scope: 'pages', page_ids: ['page-143'] },
    });
  });

  it('lists the parts with the pages each covers, and the groups of the book', async () => {
    render(WITH_VALUES);
    await openMenu('min_confidence');

    const items = [...document.body.querySelectorAll('[role="menuitem"]')].map(
      (item) => item.textContent,
    );
    expect(items).toEqual([
      'This page · p. 1431',
      'Selected pages3',
      'Odd pages305',
      'Even pages304',
      'Group · Index12',
    ]);
  });

  it('does not offer a part that has a value of the setting already', async () => {
    render(WITH_VALUES);
    await openMenu('max_angle');

    const disabled = [...document.body.querySelectorAll('[role="menuitem"]')]
      .filter((item) => item.getAttribute('aria-disabled') === 'true')
      .map((item) => item.getAttribute('data-testid'));
    expect(disabled).toEqual([
      'value-choice-page',
      'value-choice-even',
      'value-choice-group|Index',
    ]);
  });

  it('offers no selected pages while none is selected, and says when the book has no group', async () => {
    render(pageValues());
    await openMenu('min_confidence');

    const selected = document.body.querySelector('[data-testid="value-choice-selected"]');
    expect(selected?.getAttribute('aria-disabled')).toBe('true');
    expect(document.body.textContent).toContain('No groups in the book');
  });

  it('sets the value of the recipe for the odd pages when they are chosen', async () => {
    render(WITH_VALUES);
    await openMenu('min_confidence');

    await act(async () => {
      document.body.querySelector<HTMLElement>('[data-testid="value-choice-odd"]')?.click();
    });

    expect(sdk.put).toHaveBeenCalledTimes(1);
    expect(sdk.put.mock.calls[0]?.[0]).toMatchObject({
      path: { project_id: 'project', stage: 'geometry', step_id: STEP, name: 'min_confidence' },
      body: { scope: 'odd', value: 0.3 },
    });
  });

  it('sets the value for the selected pages by their identifiers', async () => {
    render(WITH_VALUES);
    await openMenu('min_confidence');

    await act(async () => {
      document.body.querySelector<HTMLElement>('[data-testid="value-choice-selected"]')?.click();
    });

    expect(sdk.put.mock.calls[0]?.[0].body).toEqual({
      scope: 'pages',
      page_ids: ['a', 'b', 'c'],
      value: 0.3,
    });
  });

  it('opens the field of a value when its chip is pressed, and sends what is typed in it once', async () => {
    vi.useFakeTimers();
    render(WITH_VALUES);
    await act(async () => {
      field('max_angle')
        ?.querySelectorAll<HTMLElement>('[data-testid="value-chip-edit"]')[0]
        ?.click();
    });
    const input = field('max_angle')?.querySelector<HTMLInputElement>(
      '[data-testid="value-editor"] input[type="number"]',
    );
    expect(input?.value).toBe('3');

    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
    for (const typed of ['7', '7.5']) {
      act(() => {
        setter?.call(input, typed);
        input?.dispatchEvent(new Event('input', { bubbles: true }));
      });
    }
    expect(sdk.put).not.toHaveBeenCalled();
    await act(async () => {
      vi.advanceTimersByTime(500);
    });

    expect(sdk.put).toHaveBeenCalledTimes(1);
    expect(sdk.put.mock.calls[0]?.[0]).toMatchObject({
      path: { step_id: STEP, name: 'max_angle' },
      body: { scope: 'even', value: 7.5 },
    });
    vi.useRealTimers();
  });
});
