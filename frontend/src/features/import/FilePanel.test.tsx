import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from '@tanstack/react-router';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PageSchema, ScanSchema, SourceSchema } from '@/api';
import { projectWith } from '@/features/about/fixtures';
import { FilePanel } from '@/features/import/FilePanel';
import { scan, source } from '@/features/import/fixtures';
import { page } from '@/features/workspace/fixtures';

/**
 * The panel of a file: its facts, what became of its pages, the box of what the file says about the book, and the two
 * actions that open the Order stage with the file named in the address.
 *
 * The links need a router, so the panel is drawn inside a small one that has the stage route and nothing else.
 */

const SUGGESTING = {
  title: 'Notes of a district teacher',
  contributors: [],
  publisher: '',
  publication_year: '',
  languages: [],
  identifiers: [],
  subjects: [],
};

describe('FilePanel', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    // The router restores the scroll position on a load, which jsdom does not implement
    vi.stubGlobal('scrollTo', vi.fn());
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  async function render(
    file: SourceSchema,
    pages: PageSchema[] = [],
    scans: ScanSchema[] = [],
    description = 'A title',
  ): Promise<void> {
    const rootRoute = createRootRoute({
      component: () => (
        <QueryClientProvider client={new QueryClient()}>
          <FilePanel
            project={projectWith({ title: description })}
            source={file}
            scans={scans}
            pages={pages}
          />
        </QueryClientProvider>
      ),
    });
    const stageRoute = createRoute({
      getParentRoute: () => rootRoute,
      path: '/projects/$projectId/stages/$stage',
    });
    const router = createRouter({
      routeTree: rootRoute.addChildren([stageRoute]),
      history: createMemoryHistory({ initialEntries: ['/'] }),
    });
    await act(async () => {
      root.render(<RouterProvider router={router} />);
    });
  }

  const text = (): string => container.textContent ?? '';
  const orderLinks = (): HTMLAnchorElement[] =>
    [...container.querySelectorAll('a')].filter((link) => link.href.includes('page-order'));

  it('states the facts of the file, with the resolution read from its scans', async () => {
    await render(
      source('f-1', { file_name: 'Notes.pdf', scan_count: 96, size_bytes: 1024 ** 2 }),
      [],
      [scan('s-1')],
    );

    expect(text()).toContain('Notes.pdf');
    expect(text()).toContain('PDF document');
    expect(text()).toContain('1 MB');
    expect(text()).toContain('96');
    expect(text()).toContain('400 dpi');
  });

  it('leaves the resolution out when no scan reports one', async () => {
    const bare = scan('s-1');
    await render(source('f-1'), [], [{ ...bare, facts: { ...bare.facts, dpi_x: null } }]);

    expect(text()).not.toContain('Resolution');
  });

  it('says where the pages of the file stand in the book', async () => {
    const pages = [0, 1, 2].map((position) =>
      page(`p-${position}`, { position, source_id: 'f-1' }),
    );
    await render(source('f-1', { scan_count: 3 }), pages);

    expect(text()).toContain('Its 3 scans became pages 1–3 of the book.');
  });

  it('says so when no page of the book comes from the file', async () => {
    await render(source('f-1'), [page('other', { source_id: 'f-2' })]);

    expect(text()).toContain('No page of the book comes from it.');
  });

  it('opens the Order stage with the file named, from both actions on its pages', async () => {
    await render(source('f-1'), [page('p-0', { source_id: 'f-1' })]);

    const links = orderLinks();
    expect(links.map((link) => link.textContent)).toEqual([
      'Show its pages in Order',
      'Put its pages somewhere else…',
    ]);
    for (const link of links) {
      expect(link.href).toContain('/projects/project-1/stages/page-order');
      expect(link.href).toContain('source=f-1');
    }
  });

  it('holds the actions on pages back while the file has none', async () => {
    await render(source('f-1'));

    expect(orderLinks()).toHaveLength(0);
    const buttons = [...container.querySelectorAll('button')].filter(
      (button) => button.textContent === 'Show its pages in Order',
    );
    expect(buttons[0]?.disabled).toBe(true);
  });

  it('shows what the file says about the book when that differs from the description', async () => {
    await render(source('f-1', { suggestion: SUGGESTING }));

    expect(container.querySelector('[data-testid="suggestion"]')).not.toBeNull();
    expect(text()).toContain('Notes of a district teacher');
    expect(text()).toContain('Use in the description');
  });

  it('shows nothing of the kind when the description already says it', async () => {
    await render(source('f-1', { suggestion: SUGGESTING }), [], [], 'Notes of a district teacher');

    expect(container.querySelector('[data-testid="suggestion"]')).toBeNull();
    expect(text()).not.toContain('Found inside the file');
  });

  it('keeps the deletion apart at the foot, with what becomes of the pages', async () => {
    await render(source('f-1'));

    const footer = container.querySelector('footer');
    expect(footer?.textContent).toContain('Delete this file…');
    expect(footer?.textContent).toContain('Its pages stay in the book with their images');
  });
});
