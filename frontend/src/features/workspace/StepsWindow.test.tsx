import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { deskew, processing, processor, recipe, step } from '@/features/processing/fixtures';
import { draftOf } from '@/features/processing/recipe';
import { row } from '@/features/workspace/fixtures';
import { StepsWindow } from '@/features/workspace/StepsWindow';

/**
 * The window of the gear: the list of the steps of the recipe, which `StepList` tests, the bar that saves the draft, and
 * the buttons for profiles.
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
    step('geometry.deskew', { step_id: 'b' }),
    step('geometry.deskew', { step_id: 'c', enabled: false }),
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
    ...document.body.querySelectorAll<HTMLElement>('[data-testid="recipe-step"]'),
  ];

  async function open(): Promise<void> {
    await act(async () => {
      byId('steps-gear')?.click();
    });
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    vi.stubGlobal('ResizeObserver', SizeObserverStandIn);
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

  it('is closed until the gear is pressed, and then names the stage and lists the steps in order, with no form of settings', async () => {
    render();
    expect(byId('steps-window')).toBeNull();

    await open();

    expect(byId('steps-window')?.textContent).toContain('Steps of Geometry');
    expect(rowsOf().map((entry) => entry.getAttribute('data-processor'))).toEqual([
      'geometry.perspective',
      'geometry.deskew',
      'geometry.deskew',
    ]);
    expect(byId('step-condition')).toBeNull();
  });

  it('shows the bar that saves only while the draft differs from the saved recipe', async () => {
    render();
    await open();
    expect(byId('recipe-save-bar')).toBeNull();

    render({ dirty: true });
    expect(byId('recipe-save-bar')?.textContent).toContain('not saved');
    expect(byId('recipe-stale-warning')?.textContent).toContain('1 page');
  });

  it('offers to keep the steps as a profile and to reset them', async () => {
    render();
    await open();

    expect(byId('steps-window')?.textContent).toContain('Save as profile');
    expect(byId('steps-window')?.textContent).not.toContain('Apply profile');
    expect(byId('steps-reset')?.textContent).toContain('Reset to the default steps');
  });
});
