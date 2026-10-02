import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RecipeRuleSchema } from '@/api';
import { recipe } from '@/features/processing/fixtures';
import { UsedFor } from '@/features/processing/UsedFor';

/**
 * What a variant is used for: its rules with the button that removes each, and the menu that adds one.
 *
 * The generated client is replaced by functions the test reads, so the request of every change is seen as the server
 * gets it.
 */

const sdk = vi.hoisted(() => ({ create: vi.fn(), put: vi.fn(), remove: vi.fn(), list: vi.fn() }));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  createRuleApiV1ProjectsProjectIdStagesStageRulesPost: sdk.create,
  putRuleApiV1ProjectsProjectIdStagesStageRulesRuleIdPut: sdk.put,
  deleteRuleApiV1ProjectsProjectIdStagesStageRulesRuleIdDelete: sdk.remove,
  listRulesApiV1ProjectsProjectIdStagesStageRulesGet: sdk.list,
}));

const TEXT = recipe('text', { name: 'Text', active: true });
const PLATES = recipe('plates', { name: 'Plates', active: false });

function rule(id: string, overrides: Partial<RecipeRuleSchema> = {}): RecipeRuleSchema {
  return {
    id,
    project_id: 'project',
    stage: 'geometry',
    condition: 'plates',
    group_label: '',
    recipe_id: 'plates',
    order: 0,
    ...overrides,
  };
}

describe('UsedFor', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  function render(rules: RecipeRuleSchema[], shown = PLATES): void {
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <UsedFor
            projectId="project"
            recipe={shown}
            recipes={[TEXT, PLATES]}
            rules={rules}
            pages={14}
          />
        </QueryClientProvider>,
      ),
    );
  }

  const byId = (id: string): HTMLElement | null =>
    container.querySelector<HTMLElement>(`[data-testid="${id}"]`);

  async function choose(id: string): Promise<void> {
    const trigger = byId('rule-add');
    await act(async () => {
      trigger?.focus();
      trigger?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    });
    const item = document.body.querySelector<HTMLElement>(`[data-testid="${id}"]`);
    await act(async () => {
      item?.click();
    });
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    for (const fake of Object.values(sdk)) {
      fake.mockReset();
      fake.mockResolvedValue({ data: rule('made') });
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

  it('lists the rules that send pages to this variant, and only those', () => {
    render([
      rule('r1'),
      rule('r2', { condition: 'covers', recipe_id: 'text' }),
      rule('r3', { condition: 'group', group_label: 'Maps', order: 1 }),
    ]);

    const texts = [...container.querySelectorAll('[data-testid="rule"]')].map(
      (item) => item.textContent,
    );
    expect(texts).toEqual(['Plates and frontispieces', 'Pages of a group · Maps']);
    expect(byId('used-for-pages')?.textContent).toBe('Made 14 pages');
  });

  it('says so when no rule sends pages to the variant', () => {
    render([rule('r2', { recipe_id: 'text' })]);

    expect(byId('used-for-empty')?.textContent).toContain('No rule sends pages here');
    expect(byId('rules')).toBeNull();
  });

  it('adds a rule for a condition the stage has no rule for', async () => {
    render([]);

    await choose('rule-add-covers');

    expect(sdk.create).toHaveBeenCalledTimes(1);
    expect(sdk.create.mock.calls[0]?.[0]).toMatchObject({
      path: { project_id: 'project', stage: 'geometry' },
      body: { condition: 'covers', group_label: '', recipe_id: 'plates' },
    });
  });

  it('moves the rule of a condition to this variant instead of adding a second rule', async () => {
    render([rule('r2', { condition: 'covers', recipe_id: 'text' })]);

    await choose('rule-add-covers');

    expect(sdk.create).not.toHaveBeenCalled();
    expect(sdk.put.mock.calls[0]?.[0]).toMatchObject({
      path: { project_id: 'project', stage: 'geometry', rule_id: 'r2' },
      body: { recipe_id: 'plates' },
    });
  });

  it('leaves a condition this variant has the rule for off the menu', async () => {
    render([rule('r1')]);

    await choose('rule-add-plates');

    expect(sdk.create).not.toHaveBeenCalled();
    expect(sdk.put).not.toHaveBeenCalled();
  });

  it('asks for the name of the group before adding a rule on a group', async () => {
    render([]);

    await choose('rule-add-group');
    expect(sdk.create).not.toHaveBeenCalled();

    const input = container.querySelector<HTMLInputElement>('[data-testid="rule-group-name"]');
    await act(async () => {
      if (input !== null) {
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
        setter?.call(input, 'Engravings');
        input.dispatchEvent(new Event('input', { bubbles: true }));
      }
    });
    await act(async () => {
      container.querySelector('form')?.requestSubmit();
    });

    expect(sdk.create.mock.calls[0]?.[0].body).toEqual({
      condition: 'group',
      group_label: 'Engravings',
      recipe_id: 'plates',
    });
  });

  it('removes a rule', async () => {
    render([rule('r1')]);

    await act(async () => {
      byId('rule-remove')?.click();
    });

    expect(sdk.remove.mock.calls[0]?.[0]).toMatchObject({
      path: { project_id: 'project', stage: 'geometry', rule_id: 'r1' },
    });
  });
});
