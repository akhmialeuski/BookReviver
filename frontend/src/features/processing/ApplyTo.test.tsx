import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RecipeRuleSchema } from '@/api';
import { ApplyTo } from '@/features/processing/ApplyTo';
import { processing, recipe } from '@/features/processing/fixtures';
import { page, row } from '@/features/workspace/fixtures';
import { joinRows } from '@/features/workspace/strip';

/**
 * Giving the variant in the panel to more pages: a pin for this page, the selected pages and all pages, and a rule for
 * every page of the kind of the open one, which the stage is then run on.
 *
 * The generated client is replaced by functions the test reads, so each request is seen as the server gets it.
 */

const sdk = vi.hoisted(() => ({
  run: vi.fn(),
  jobs: vi.fn(),
  rules: vi.fn(),
  create: vi.fn(),
  put: vi.fn(),
  unpin: vi.fn(),
}));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  runStageApiV1ProjectsProjectIdStagesStageRunPost: sdk.run,
  listProjectJobsApiV1ProjectsProjectIdJobsGet: sdk.jobs,
  listRulesApiV1ProjectsProjectIdStagesStageRulesGet: sdk.rules,
  createRuleApiV1ProjectsProjectIdStagesStageRulesPost: sdk.create,
  putRuleApiV1ProjectsProjectIdStagesStageRulesRuleIdPut: sdk.put,
  unpinStageApiV1ProjectsProjectIdPagesPageIdStagesStagePinDelete: sdk.unpin,
}));

const TEXT = recipe('text', { name: 'Text', active: true, created_at: '2026-10-01T00:00:00Z' });
const PLATES = recipe('plates', {
  name: 'Plates',
  active: false,
  created_at: '2026-10-01T00:01:00Z',
});

const ITEMS = joinRows(
  [
    page('a', { position: 0 }),
    page('b', { position: 1, kind: 'plate' }),
    page('c', { position: 2, kind: 'frontispiece' }),
    page('d', { position: 3, kind: 'plate', origin: 'placeholder' }),
  ],
  [
    row('a', { recipe_id: 'text' }),
    row('b', { recipe_id: 'plates', pinned: true }),
    row('c', { recipe_id: 'text' }),
  ],
);

function rule(id: string, overrides: Partial<RecipeRuleSchema> = {}): RecipeRuleSchema {
  return {
    id,
    project_id: 'project',
    stage: 'geometry',
    condition: 'plates',
    group_label: '',
    recipe_id: 'text',
    order: 0,
    ...overrides,
  };
}

describe('ApplyTo', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  async function render(
    options: { current?: number; dirty?: boolean; selected?: string[] } = {},
  ): Promise<void> {
    const item = ITEMS[options.current ?? 0];
    if (item === undefined) {
      throw new Error('No such page in the fixture.');
    }
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <ApplyTo
            processing={processing({
              recipe: PLATES,
              recipes: [TEXT, PLATES],
              dirty: options.dirty ?? false,
            })}
            items={ITEMS}
            item={item}
            selected={new Set(options.selected ?? [])}
          />
        </QueryClientProvider>,
      );
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  const byId = (id: string): HTMLButtonElement | null =>
    container.querySelector<HTMLButtonElement>(`[data-testid="${id}"]`);

  async function choose(id: string): Promise<void> {
    const trigger = byId('apply-menu');
    await act(async () => {
      trigger?.focus();
      trigger?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    });
    const item = document.body.querySelector<HTMLElement>(`[data-testid="${id}"]`);
    await act(async () => {
      item?.click();
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    for (const fake of Object.values(sdk)) {
      fake.mockReset();
    }
    sdk.run.mockResolvedValue({ data: { id: 'job' } });
    sdk.jobs.mockResolvedValue({ data: { items: [], total: 0, page: 1, size: 20, pages: 1 } });
    sdk.rules.mockResolvedValue({ data: { items: [], total: 0, page: 1, size: 100, pages: 1 } });
    sdk.create.mockResolvedValue({ data: rule('made', { recipe_id: 'plates' }) });
    sdk.put.mockResolvedValue({ data: rule('r1', { recipe_id: 'plates' }) });
    sdk.unpin.mockResolvedValue({ data: row('b') });
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

  it('shows the variant of the page, and that it is pinned', async () => {
    await render({ current: 1 });

    expect(byId('page-variant')?.textContent).toBe('Plates');
    expect(byId('page-variant-source')?.textContent).toBe('Pinned to this page');
  });

  it('shows a page the rules gave its variant as the rules of the book', async () => {
    await render({ current: 0 });

    expect(byId('page-variant')?.textContent).toBe('Text');
    expect(byId('page-variant-source')?.textContent).toBe('By the rules of the book');
    expect(byId('use-rules')).toBeNull();
  });

  it('pins the variant to this page by a run of that variant with the pin', async () => {
    await render({ current: 0 });

    await choose('apply-page');

    expect(sdk.run.mock.calls[0]?.[0]).toMatchObject({
      path: { project_id: 'project', stage: 'geometry' },
      body: { recipe_id: 'plates', pin: true, page_ids: ['a'] },
    });
  });

  it('pins it to the selected pages', async () => {
    await render({ current: 0, selected: ['b', 'c'] });

    await choose('apply-selected');

    expect(sdk.run.mock.calls[0]?.[0].body).toEqual({
      recipe_id: 'plates',
      pin: true,
      page_ids: ['b', 'c'],
    });
  });

  it('keeps the choice of the selected pages off while none is selected', async () => {
    await render({ current: 0 });
    const trigger = byId('apply-menu');
    await act(async () => {
      trigger?.focus();
      trigger?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    });

    expect(
      document.body.querySelector('[data-testid="apply-selected"]')?.getAttribute('data-disabled'),
    ).toBe('');
  });

  it('pins it to every page by naming no pages', async () => {
    await render({ current: 0 });

    await choose('apply-all');

    expect(sdk.run.mock.calls[0]?.[0].body).toEqual({ recipe_id: 'plates', pin: true });
  });

  it('makes a rule for the kind of the page, and runs the stage on the pages of that kind by the rules', async () => {
    await render({ current: 1 });

    await choose('apply-kind');

    expect(sdk.create.mock.calls[0]?.[0]).toMatchObject({
      path: { project_id: 'project', stage: 'geometry' },
      body: { condition: 'plates', recipe_id: 'plates' },
    });
    expect(sdk.run.mock.calls[0]?.[0].body).toEqual({ page_ids: ['b', 'c'] });
  });

  it('moves the rule of the kind to the variant when the stage has one already', async () => {
    sdk.rules.mockResolvedValue({
      data: { items: [rule('r1')], total: 1, page: 1, size: 100, pages: 1 },
    });
    await render({ current: 1 });

    await choose('apply-kind');

    expect(sdk.create).not.toHaveBeenCalled();
    expect(sdk.put.mock.calls[0]?.[0]).toMatchObject({
      path: { project_id: 'project', stage: 'geometry', rule_id: 'r1' },
      body: { recipe_id: 'plates' },
    });
    expect(sdk.run.mock.calls[0]?.[0].body).toEqual({ page_ids: ['b', 'c'] });
  });

  it('has no rule for a kind that no condition names', async () => {
    await render({ current: 0 });
    const trigger = byId('apply-menu');
    await act(async () => {
      trigger?.focus();
      trigger?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    });

    expect(
      document.body.querySelector('[data-testid="apply-kind"]')?.getAttribute('data-disabled'),
    ).toBe('');
  });

  it('takes the pin off with "Use the book\'s rules"', async () => {
    await render({ current: 1 });

    await act(async () => {
      byId('use-rules')?.click();
    });

    expect(sdk.unpin.mock.calls[0]?.[0]).toMatchObject({
      path: { project_id: 'project', page_id: 'b', stage: 'geometry' },
    });
  });

  it('keeps the menu off while the draft has changes that are not saved', async () => {
    await render({ current: 0, dirty: true });

    expect(byId('apply-menu')?.disabled).toBe(true);
  });
});
