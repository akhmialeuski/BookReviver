import { QueryClient, QueryClientProvider, type UseQueryResult } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NumberingPanel } from '@/features/order/NumberingPanel';
import { newDraft } from '@/features/order/numbering';
import { page } from '@/features/workspace/fixtures';

/**
 * What the panel of the Order stage puts in the slots of the layout while pages are numbered: the title of the numbering
 * in the step slot, its fields in the settings frame, the count and the buttons in the footer, and no page section.
 */

describe('NumberingPanel slots', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  const pages = [page('a', { position: 0 }), page('b', { position: 1 })];

  function render(): void {
    const draft = newDraft(pages);
    if (draft === null) {
      throw new Error('The book of the test has no pages.');
    }
    const preview = {
      data: new Map([['a', '1']]),
      isFetching: false,
      isError: false,
    } as unknown as UseQueryResult<Map<string, string>>;
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <NumberingPanel
            projectId="book"
            pages={pages}
            draft={draft}
            preview={preview}
            gaps={[]}
            adding={false}
            onDraft={() => undefined}
            onClose={() => undefined}
            onAddMissing={() => undefined}
          />
        </QueryClientProvider>,
      ),
    );
  }

  const find = (testId: string): HTMLElement | null =>
    container.querySelector<HTMLElement>(`[data-testid="${testId}"]`);

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

  it('puts the title in the step slot, the fields in the settings frame and the count in the footer', () => {
    render();

    expect(find('step-panel-title')?.textContent).toBe('Number pages');
    expect(find('panel-step')?.textContent).toContain('Makes a section');
    expect(find('panel-settings')?.contains(find('numbering-panel'))).toBe(true);
    expect(find('panel-settings')?.textContent).toContain('Style');
    expect(find('stage-panel')?.querySelector('footer')?.contains(find('numbering-counts'))).toBe(
      true,
    );
  });

  it('has no page section and no title of its own inside the settings frame', () => {
    render();

    expect(find('panel-page')).toBeNull();
    expect(find('panel-settings')?.querySelector('h3')).toBeNull();
  });
});
