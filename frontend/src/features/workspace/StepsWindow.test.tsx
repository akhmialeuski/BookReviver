import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { deskew, processing, processor, recipe, step } from '@/features/processing/fixtures';
import type { OrderIssue } from '@/features/processing/order';
import { draftOf } from '@/features/processing/recipe';
import { row } from '@/features/workspace/fixtures';
import { StepsWindow } from '@/features/workspace/StepsWindow';

/**
 * The window of the gear: the steps of the recipe as a list with a condition, a switch and a cross each, the marks of the
 * steps that stand off their place, the bar that saves the draft, and the buttons for profiles.
 *
 * Radix measures a switch, which jsdom cannot, so the observer it asks for is given a stand-in.
 */

class SizeObserverStandIn {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

const SAVED = recipe('r1', {
  steps: [
    step('geometry.perspective', { step_id: 'a' }),
    step('geometry.deskew', { step_id: 'b', applies_to: 'text' }),
    step('geometry.deskew', { step_id: 'c', applies_to: 'pictures', enabled: false }),
  ],
});
const CATALOGUE = [
  processor('geometry.perspective', {
    title: 'Perspective',
    summary: 'Makes the sheet a rectangle',
  }),
  deskew({ title: 'Deskew' }),
];

describe('StepsWindow', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;
  const actions = {
    toggle: vi.fn(),
    remove: vi.fn(),
    condition: vi.fn(),
    setOrderMode: vi.fn(),
  };

  function render(overrides: Parameters<typeof processing>[0] = {}): void {
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <StepsWindow
            processing={processing({
              catalogue: CATALOGUE,
              recipe: SAVED,
              recipes: [SAVED],
              steps: draftOf(SAVED),
              ...actions,
              ...overrides,
            })}
            rows={[row('a', { recipe_id: 'r1', status: 'fresh' })]}
          />
        </QueryClientProvider>,
      ),
    );
  }

  const byId = (id: string): HTMLElement | null =>
    document.body.querySelector<HTMLElement>(`[data-testid="${id}"]`);
  const rowsOf = (): HTMLElement[] => [
    ...document.body.querySelectorAll<HTMLElement>('[data-testid="window-step"]'),
  ];

  async function open(): Promise<void> {
    await act(async () => {
      byId('steps-gear')?.click();
    });
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    vi.stubGlobal('ResizeObserver', SizeObserverStandIn);
    for (const fake of Object.values(actions)) {
      fake.mockReset();
    }
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

  it('is closed until the gear is pressed, and then names the stage and lists the steps in order', async () => {
    render();
    expect(byId('steps-window')).toBeNull();

    await open();

    expect(byId('steps-window')?.textContent).toContain('Steps of Geometry');
    expect(rowsOf().map((entry) => entry.getAttribute('data-processor'))).toEqual([
      'geometry.perspective',
      'geometry.deskew',
      'geometry.deskew',
    ]);
    expect(rowsOf()[0]?.textContent).toContain('Makes the sheet a rectangle');
  });

  it('shows the condition of each step and whether it is on', async () => {
    render();
    await open();

    const conditions = rowsOf().map(
      (entry) =>
        entry.querySelector<HTMLSelectElement>('[data-testid="window-step-condition"]')?.value,
    );
    const switches = rowsOf().map((entry) =>
      entry.querySelector('[data-testid="window-step-enabled"]')?.getAttribute('aria-checked'),
    );
    expect(conditions).toEqual(['all', 'text', 'pictures']);
    expect(switches).toEqual(['true', 'true', 'false']);
  });

  it('changes the draft by the condition, the switch and the cross of a step', async () => {
    render();
    await open();
    const second = rowsOf()[1];

    const select = second?.querySelector<HTMLSelectElement>(
      '[data-testid="window-step-condition"]',
    );
    await act(async () => {
      if (select !== null && select !== undefined) {
        select.value = 'pictures';
        select.dispatchEvent(new Event('change', { bubbles: true }));
      }
    });
    await act(async () => {
      second?.querySelector<HTMLElement>('[data-testid="window-step-enabled"]')?.click();
    });
    await act(async () => {
      second?.querySelector<HTMLElement>('[data-testid="window-step-remove"]')?.click();
    });

    expect(actions.condition).toHaveBeenCalledWith('step-1', 'pictures');
    expect(actions.toggle).toHaveBeenCalledWith('step-1');
    expect(actions.remove).toHaveBeenCalledWith('step-1');
  });

  it('marks a step that stands off its place, with the reason, and offers the usual order', async () => {
    const issue: OrderIssue = {
      stepId: 'step-1',
      otherId: 'step-0',
      kind: 'usual',
      reason: 'Deskew usually follows Perspective.',
    };
    render({ orderIssues: new Map([['step-1', [issue]]]) });
    await open();

    const mark = rowsOf()[1]?.querySelector('[data-testid="window-step-order-mark"]');
    expect(mark?.getAttribute('data-kind')).toBe('usual');
    expect(mark?.getAttribute('title')).toBe('Deskew usually follows Perspective.');
    expect(byId('window-restore-order')).not.toBeNull();
  });

  it('shows the bar that saves only while the draft differs from the saved recipe', async () => {
    render();
    await open();
    expect(byId('recipe-save-bar')).toBeNull();

    render({ dirty: true });
    expect(byId('recipe-save-bar')?.textContent).toContain('not saved');
    expect(byId('recipe-stale-warning')?.textContent).toContain('1 page');
  });

  it('switches the order the draft is saved in, and offers the profiles of the account', async () => {
    render();
    await open();

    await act(async () => {
      byId('window-order-free')?.click();
    });

    expect(actions.setOrderMode).toHaveBeenCalledWith('free');
    expect(byId('steps-window')?.textContent).toContain('Save as profile');
    expect(byId('steps-window')?.textContent).toContain('Apply profile');
  });
});
