import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PageSchema } from '@/api';
import { SelectionPanel } from '@/features/order/SelectionPanel';
import { page } from '@/features/workspace/fixtures';

/**
 * What the panel of the Order stage puts in the slots of the layout while no numbering is open: the pagination and the
 * controls that change the selected pages in the settings frame, the selection with its thumbnails in the one page
 * section, and the places to check as its facts, last, right above the history.
 */

describe('SelectionPanel slots', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  function render(selected: readonly PageSchema[], withPlaces = true): void {
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <SelectionPanel
            projectId="book"
            selected={selected}
            thumbnails={new Map()}
            gaps={[]}
            missing={[]}
            places={withPlaces ? [{ kind: 'gap', cellId: 'gap-a' }] : []}
            blankPages={[]}
            onClear={() => undefined}
            onMove={() => undefined}
            onNumber={() => undefined}
            onInsert={() => undefined}
            onAttach={() => undefined}
            onDelete={() => undefined}
            onShowPlace={() => undefined}
            pagination={<section data-testid="pagination-stub">Pagination</section>}
          />
        </QueryClientProvider>,
      ),
    );
  }

  const find = (testId: string): HTMLElement | null =>
    container.querySelector<HTMLElement>(`[data-testid="${testId}"]`);
  const areaIds = (): (string | null)[] =>
    [...(find('stage-panel-scroll')?.children ?? [])].map((child) =>
      child.getAttribute('data-testid'),
    );

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
    client = new QueryClient();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    client.clear();
    vi.unstubAllGlobals();
  });

  it('fills the settings frame and the page section, and ends with the history', () => {
    render([page('a'), page('b')]);

    expect(areaIds()).toEqual(['panel-settings', 'panel-page', 'page-history']);
    const settings = find('panel-settings');
    expect(settings?.contains(find('pagination-stub'))).toBe(true);
    expect(settings?.textContent).toContain('What these pages are');
    expect(settings?.textContent).toContain('Part of the book');
    expect(settings?.textContent).toContain('Notes');
    expect(settings?.textContent).toContain('Move to another place');
  });

  it('names the selection in the one page section, with the button that clears it beside the title', () => {
    render([page('a'), page('b')]);

    const section = find('panel-page');
    expect(section?.querySelector('h3')?.textContent).toContain('2 pages selected');
    expect(section?.querySelector('button')?.textContent).toBe('Clear the selection');
    expect(section?.querySelector('ul')).not.toBeNull();
  });

  it('puts the places to check last in the page section, right above the history', () => {
    render([page('a')]);

    expect(find('panel-page')?.lastElementChild).toBe(find('panel-facts'));
    expect(find('panel-facts')?.contains(find('places-to-check'))).toBe(true);
    expect(find('panel-page')?.nextElementSibling).toBe(find('page-history'));
  });

  it('has no heading for the actions, which stand in the settings frame without one', () => {
    render([page('a')]);

    const headings = [...container.querySelectorAll('h3')].map((heading) => heading.textContent);
    expect(headings).not.toContain('Actions');
    expect(find('panel-settings')?.querySelector('h3')).toBeNull();
  });

  it('says no page is selected, with the sentence that asks for a selection, and keeps the pagination', () => {
    render([]);

    expect(areaIds()).toEqual(['panel-settings', 'panel-page', 'page-history']);
    expect(find('panel-settings')?.contains(find('pagination-stub'))).toBe(true);
    expect(find('panel-page')?.querySelector('h3')?.textContent).toBe('No pages selected');
    expect(find('panel-page')?.textContent).toContain('Select pages to change');
    expect(find('panel-page')?.lastElementChild).toBe(find('panel-facts'));
  });

  it('draws no facts when no place asks for a look', () => {
    render([page('a')], false);

    expect(find('panel-facts')).toBeNull();
    expect(find('places-to-check')).toBeNull();
  });
});
