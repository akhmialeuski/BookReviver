import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PageSchema } from '@/api';
import { SelectionPanel } from '@/features/order/SelectionPanel';
import { images, page } from '@/features/workspace/fixtures';

/**
 * The thumbnails of the selected pages in the panel of the Order stage: the picture of each page at the stage, which the
 * rows of the stage give, and not the image of the page, which is the latest result of the book.
 */

describe('SelectionPanel thumbnails', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  function render(selected: readonly PageSchema[], thumbnails: ReadonlyMap<string, string>): void {
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <SelectionPanel
            projectId="book"
            selected={selected}
            thumbnails={thumbnails}
            gaps={[]}
            missing={[]}
            places={[]}
            blankPages={[]}
            onClear={() => undefined}
            onMove={() => undefined}
            onNumber={() => undefined}
            onInsert={() => undefined}
            onAttach={() => undefined}
            onDelete={() => undefined}
            onShowPlace={() => undefined}
            pagination={null}
          />
        </QueryClientProvider>,
      ),
    );
  }

  const sources = (): (string | null)[] =>
    [...container.querySelectorAll('[data-testid="panel-page"] ul img')].map((img) =>
      img.getAttribute('src'),
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

  it('draws the picture of each page at the stage, not the image the page has from a later stage', () => {
    render(
      [page('a', { images: images('geometry-a') }), page('b', { images: images('geometry-b') })],
      new Map([
        ['a', '/page-order/a/thumb'],
        ['b', '/page-order/b/thumb'],
      ]),
    );

    expect(sources()).toEqual(['/page-order/a/thumb', '/page-order/b/thumb']);
  });

  it('draws no picture for a page the stage has none of, whatever the page images say', () => {
    render([page('a', { images: images('geometry-a') })], new Map());

    expect(sources()).toEqual([]);
  });
});
