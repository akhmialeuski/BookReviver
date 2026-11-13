import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { processing, processor, recipe, step } from '@/features/processing/fixtures';
import { draftOf } from '@/features/processing/recipe';
import { StepTypeMenu } from '@/features/processing/StepTypeMenu';
import { MESSAGES } from '@/shared/messages';

/**
 * The menu on the card of a step that replaces its type: the processors of the stage the server accepts where the step
 * stands, its own marked, and what a choice sends.
 *
 * C must stand after B, so in the usual order the first step, which stands before B, cannot become a C.
 */

const sdk = vi.hoisted(() => ({ replace: vi.fn() }));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  replaceStepTypeApiV1BooksBookIdStagesStageRecipesRecipeIdStepsStepIdTypePut: sdk.replace,
}));

class SizeObserverStandIn {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

const C_REASON = 'C works on what B leaves, so it cannot come before B.';

const CATALOGUE = [
  processor('geometry.a', { title: 'A' }),
  processor('geometry.b', { title: 'B' }),
  processor('geometry.c', {
    title: 'C',
    requires_after: [{ processor_key: 'geometry.b', reason: C_REASON }],
  }),
];

const SAVED = recipe('r1', {
  steps: [step('geometry.a', { step_id: 'sa' }), step('geometry.b', { step_id: 'sb' })],
});

describe('StepTypeMenu', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  function render(overrides: Parameters<typeof processing>[0] = {}): void {
    const shown = processing({
      catalogue: CATALOGUE,
      recipe: SAVED,
      recipes: [SAVED],
      steps: draftOf(SAVED),
      ...overrides,
    });
    const first = shown.steps[0];
    if (first === undefined) {
      return;
    }
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <StepTypeMenu processing={shown} step={first} title="A" />
        </QueryClientProvider>,
      ),
    );
  }

  async function openMenu(): Promise<void> {
    const trigger = container.querySelector<HTMLElement>('[data-testid="step-type"]');
    await act(async () => {
      trigger?.focus();
      trigger?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    });
  }

  const options = (): HTMLElement[] => [
    ...document.body.querySelectorAll<HTMLElement>('[data-testid="step-type-option"]'),
  ];

  async function choose(key: string): Promise<void> {
    await act(async () => {
      options()
        .find((option) => option.dataset.processor === key)
        ?.click();
    });
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    vi.stubGlobal('ResizeObserver', SizeObserverStandIn);
    sdk.replace.mockReset();
    sdk.replace.mockResolvedValue({ data: SAVED });
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

  it('is named for the step it changes', () => {
    render();

    const trigger = container.querySelector('[data-testid="step-type"]');
    expect(trigger?.getAttribute('aria-label')).toBe(MESSAGES.processing.steps.type.open('A'));
  });

  it('lists the types the usual order accepts at the place of the step, its own marked', async () => {
    render();
    await openMenu();

    expect(options().map((option) => option.dataset.processor)).toEqual([
      'geometry.a',
      'geometry.b',
    ]);
    expect(options().map((option) => option.dataset.current)).toEqual(['true', 'false']);
    expect(options()[0]?.textContent).toContain(MESSAGES.processing.steps.type.current);
  });

  it('lists every type of the stage in the free order', async () => {
    render({ orderMode: 'free' });
    await openMenu();

    expect(options().map((option) => option.dataset.processor)).toEqual([
      'geometry.a',
      'geometry.b',
      'geometry.c',
    ]);
  });

  it('replaces the type of the saved step in the order the panel keeps', async () => {
    render();
    await openMenu();
    await choose('geometry.b');

    expect(sdk.replace).toHaveBeenCalledWith(
      expect.objectContaining({
        path: { book_id: 'book', stage: 'geometry', recipe_id: 'r1', step_id: 'sa' },
        body: { processor_key: 'geometry.b', order: 'usual' },
      }),
    );
  });

  it('sends nothing for the type the step has', async () => {
    render();
    await openMenu();
    await choose('geometry.a');

    expect(sdk.replace).not.toHaveBeenCalled();
  });

  it('waits while the draft holds changes, and says why', async () => {
    render({ dirty: true });
    await openMenu();
    await choose('geometry.b');

    expect(sdk.replace).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain(MESSAGES.processing.steps.type.saveFirst);
  });

  it('is not drawn for a step the recipe has not saved', () => {
    render({ steps: draftOf(SAVED).map((draft) => ({ ...draft, stepId: null })) });

    expect(container.querySelector('[data-testid="step-type"]')).toBeNull();
  });
});
