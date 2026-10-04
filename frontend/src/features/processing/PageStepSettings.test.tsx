import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { deskew } from '@/features/processing/fixtures';
import { PageStepSettings } from '@/features/processing/PageStepSettings';
import type { StepDraft } from '@/features/processing/recipe';

/**
 * The settings of one step that only the open page uses: the fields it changes with the way back to the recipe, the
 * form that changes more, and the step that is not saved yet, which has none.
 *
 * Radix measures the thumb of a slider, which jsdom cannot, so the observer it asks for is given a stand-in.
 */

const sdk = vi.hoisted(() => ({
  put: vi.fn(),
  remove: vi.fn(),
}));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  putSettingApiV1ProjectsProjectIdPagesPageIdSettingsStageStepIdNamePut: sdk.put,
  deleteSettingApiV1ProjectsProjectIdPagesPageIdSettingsStageStepIdNameDelete: sdk.remove,
}));

class SizeObserverStandIn {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

const SAVED: StepDraft = {
  id: 'step-0',
  stepId: 'step-of-deskew',
  processorKey: 'geometry.deskew',
  params: { max_angle: 5, min_confidence: 0.3 },
  enabled: true,
  appliesTo: 'all',
};

describe('PageStepSettings', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  function render(step = SAVED, pageValues: Record<string, unknown> = { max_angle: 3 }): void {
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <PageStepSettings
            processing={{ projectId: 'project', stage: 'geometry' }}
            step={step}
            processor={deskew()}
            pageId="page"
            pageValues={pageValues}
          />
        </QueryClientProvider>,
      ),
    );
  }

  const byId = (id: string): HTMLElement | null =>
    container.querySelector<HTMLElement>(`[data-testid="${id}"]`);

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    vi.stubGlobal('ResizeObserver', SizeObserverStandIn);
    sdk.put.mockReset();
    sdk.remove.mockReset();
    sdk.put.mockResolvedValue({ data: {} });
    sdk.remove.mockResolvedValue({ data: undefined });
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

  it('lists each field the page changes with its title and its value', () => {
    render();

    expect(byId('page-settings-list')?.textContent).toContain('Largest slant: 3');
    expect(byId('page-settings-none')).toBeNull();
  });

  it('says the page uses the recipe when it changes nothing', () => {
    render(SAVED, {});

    expect(byId('page-settings-none')?.textContent).toContain('uses the value of the recipe');
    expect(byId('page-settings-list')).toBeNull();
  });

  it('takes a field back from the page by the identifier of the step', async () => {
    render();

    await act(async () => {
      byId('page-settings-reset')?.click();
    });

    expect(sdk.remove).toHaveBeenCalledTimes(1);
    expect(sdk.remove.mock.calls[0]?.[0]).toMatchObject({
      path: {
        project_id: 'project',
        page_id: 'page',
        stage: 'geometry',
        step_id: SAVED.stepId,
        name: 'max_angle',
      },
    });
  });

  it('opens the form of the step with the values the page runs with, marking the field it changes', () => {
    render();
    expect(container.querySelector('form')).toBeNull();

    act(() => byId('page-settings-edit')?.click());

    const labels = [...container.querySelectorAll('form label')].map((label) => label.textContent);
    expect(labels).toEqual(['Largest slant · changed for this page', 'Least confidence']);
    const [first] = container.querySelectorAll<HTMLInputElement>('form input[type="number"]');
    expect(first?.value).toBe('3');
  });

  it('sets only the field that was typed into, for this page', async () => {
    render();
    act(() => byId('page-settings-edit')?.click());

    const [first] = container.querySelectorAll<HTMLInputElement>('form input[type="number"]');
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
    await act(async () => {
      setter?.call(first, '7.5');
      first?.dispatchEvent(new Event('input', { bubbles: true }));
    });

    expect(sdk.put).toHaveBeenCalledTimes(1);
    expect(sdk.put.mock.calls[0]?.[0]).toMatchObject({
      path: { page_id: 'page', stage: 'geometry', name: 'max_angle' },
      body: { value: 7.5 },
    });
  });

  it('offers a carry-over for each field the page changes, and none when it changes nothing', () => {
    render();
    expect(container.querySelectorAll('[data-testid="carry-menu"]')).toHaveLength(1);
    expect(byId('carry-overwrite')).not.toBeNull();

    render(SAVED, {});

    expect(byId('carry-menu')).toBeNull();
    expect(byId('carry-overwrite')).toBeNull();
  });

  it('offers a carry-over for every field the page changes', () => {
    render(SAVED, { max_angle: 3, min_confidence: 0.5 });

    expect(container.querySelectorAll('[data-testid="carry-menu"]')).toHaveLength(2);
  });

  it('offers the reset of the step under its settings, and none for a step that is not saved yet', () => {
    render();
    expect(byId('reset-menu')?.getAttribute('aria-label')).toBe('Reset Deskew to the defaults');

    render({ ...SAVED, stepId: null });
    expect(byId('reset-menu')).toBeNull();
  });

  it('offers no settings for a step that is not saved yet', () => {
    render({ ...SAVED, stepId: null });

    expect(byId('page-settings-save-first')?.textContent).toContain('Save the recipe first');
    expect(byId('page-settings')).toBeNull();
  });
});
