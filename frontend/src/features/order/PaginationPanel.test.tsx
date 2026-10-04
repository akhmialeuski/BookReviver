import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PageSchema, PaginationSectionSchema } from '@/api';
import { PaginationPanel } from '@/features/order/PaginationPanel';
import { SECTION_TONES, sectionSpans } from '@/features/order/sections';
import { page, section } from '@/features/workspace/fixtures';
import { ProblemError } from '@/shared/http/problem';
import { HttpStatus } from '@/shared/http/status';

/**
 * The pagination of the book on the Order stage: the list of its sections with their bar, place, summary and numbers,
 * and the form that makes, changes and deletes them.
 *
 * The generated client is replaced by functions the test reads, so the requests are seen as the server gets them.
 */

const sdk = vi.hoisted(() => ({ create: vi.fn(), put: vi.fn(), remove: vi.fn() }));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  createPaginationSectionApiV1ProjectsProjectIdPaginationSectionsPost: sdk.create,
  putPaginationSectionApiV1ProjectsProjectIdPaginationSectionsSectionIdPut: sdk.put,
  deletePaginationSectionApiV1ProjectsProjectIdPaginationSectionsSectionIdDelete: sdk.remove,
}));

const PROJECT = 'book';
const PAGES: PageSchema[] = [
  page('p0', { position: 0, kind: 'cover' }),
  page('p1', { position: 1, label: '[i]' }),
  page('p2', { position: 2, label: '[ii]' }),
  page('p3', { position: 3, label: 'vi' }),
  page('p4', { position: 4, label: 'Plate I', kind: 'plate' }),
  page('p5', { position: 5, label: '1' }),
];
const SECTIONS: PaginationSectionSchema[] = [
  section('cover', 'p0', { name: 'Cover', display: 'not-counted' }),
  section('front', 'p1', { name: 'Half title', display: 'counted', style: 'roman-lower' }),
  section('preface', 'p3', { name: 'Preface', style: 'roman-lower', start: 6 }),
  section('text', 'p5', { name: '' }),
  section('plates', 'p1', {
    name: 'Plates',
    style: 'roman-upper',
    prefix: 'Plate ',
    kinds: ['plate'],
  }),
];

describe('PaginationPanel', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  /** The dialog is drawn into the body, outside the container the panel is rendered into. */
  const dialog = (): HTMLElement | null =>
    document.body.querySelector<HTMLElement>('[data-testid="section-dialog"]');
  const rows = (): HTMLElement[] => [
    ...container.querySelectorAll<HTMLElement>('[data-testid="section-row"]'),
  ];
  const field = (label: string): HTMLInputElement | HTMLSelectElement => {
    const found = [...(dialog()?.querySelectorAll('label') ?? [])].find(
      (candidate) => candidate.textContent === label,
    );
    const input = found === undefined ? null : document.getElementById(found.htmlFor);
    if (!(input instanceof HTMLInputElement || input instanceof HTMLSelectElement)) {
      throw new Error(`The dialog has no field called ${label}.`);
    }
    return input;
  };
  const button = (name: string): HTMLButtonElement => {
    const found = [...document.body.querySelectorAll('button')].find(
      (candidate) => candidate.textContent === name,
    );
    if (found === undefined) {
      throw new Error(`No button is called ${name}.`);
    }
    return found;
  };

  function render(
    options: { pages?: PageSchema[]; sections?: PaginationSectionSchema[]; selected?: string } = {},
  ): void {
    const pages = options.pages ?? PAGES;
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <PaginationPanel
            projectId={PROJECT}
            pages={pages}
            spans={sectionSpans(pages, options.sections ?? SECTIONS)}
            selectedId={options.selected}
          />
        </QueryClientProvider>,
      ),
    );
  }

  async function type(input: HTMLInputElement | HTMLSelectElement, value: string): Promise<void> {
    await act(async () => {
      const prototype =
        input instanceof HTMLSelectElement
          ? HTMLSelectElement.prototype
          : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(prototype, 'value')?.set?.call(input, value);
      input.dispatchEvent(
        new Event(input instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }),
      );
    });
  }

  async function press(target: HTMLElement): Promise<void> {
    await act(async () => target.click());
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    for (const fake of Object.values(sdk)) {
      fake.mockReset();
      fake.mockResolvedValue({ data: undefined });
    }
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

  it('lists the sections in book order with their place, their numbers and what they do to the pages', () => {
    render();

    expect(
      rows().map((row) => row.querySelector('[data-testid="section-name"]')?.textContent),
    ).toEqual(['Cover', 'Half title', 'Plates', 'Preface', 'Section 5']);
    expect(
      rows().map((row) => row.querySelector('[data-testid="section-numbers"]')?.textContent),
    ).toEqual(['—', '[i]–[ii]', 'Plate I', 'vi', '1']);
    expect(rows()[0]?.textContent).toContain('#1');
    expect(rows()[0]?.textContent).toContain('Not counted');
    expect(rows()[1]?.textContent).toContain('Counted, not printed');
    expect(rows()[3]?.textContent).toContain('Roman, lower case from 6');
    expect(rows()[4]?.textContent).toContain('Arabic');
  });

  it('says which kinds a series takes where the others say which pages they cover', () => {
    render();

    expect(rows()[2]?.textContent).toContain('Plate');
    expect(rows()[2]?.textContent).toContain('Own sequence, Roman, upper case');
    expect(rows()[2]?.textContent).not.toContain('#');
  });

  it('draws the bar of each section in its colour, and grey for a section that is not counted', () => {
    render();

    expect(rows()[0]?.className).toContain('border-l-gray-400');
    expect(rows()[1]?.className).toContain(SECTION_TONES[0]?.edge);
    expect(rows()[3]?.className).toContain(SECTION_TONES[2]?.edge);
  });

  it('asks for the first section when the book has none', () => {
    render({ sections: [] });

    expect(container.querySelector('[data-testid="sections-empty"]')).not.toBeNull();
    expect(rows()).toHaveLength(0);
  });

  it('offers no section for a book without pages', () => {
    render({ pages: [], sections: [] });

    expect(
      container.querySelector<HTMLButtonElement>('[data-testid="section-add"]')?.disabled,
    ).toBe(true);
  });

  it('starts a new section at the selected page, and at the first page when none is selected', async () => {
    render({ selected: 'p3' });
    await press(container.querySelector<HTMLElement>('[data-testid="section-add"]') as HTMLElement);
    expect(dialog()?.textContent).toContain('New section');
    expect((field('Starts at') as HTMLSelectElement).value).toBe('p3');
    await press(button('Cancel'));
    expect(dialog()).toBeNull();

    render();
    await press(container.querySelector<HTMLElement>('[data-testid="section-add"]') as HTMLElement);
    expect((field('Starts at') as HTMLSelectElement).value).toBe('p0');
  });

  it('makes the section the form describes', async () => {
    render({ selected: 'p3' });
    await press(container.querySelector<HTMLElement>('[data-testid="section-add"]') as HTMLElement);

    await type(field('Name'), ' Foreword ');
    await type(field('How the pages show their numbers'), 'counted');
    await type(field('First number'), '2');
    await type(field('Prefix'), 'Pt ');
    await press(button('Save the section'));

    expect(sdk.create).toHaveBeenCalledWith(
      expect.objectContaining({
        path: { project_id: PROJECT },
        body: {
          first_page_id: 'p3',
          name: 'Foreword',
          style: 'arabic',
          start: 2,
          prefix: 'Pt ',
          display: 'counted',
          kinds: [],
        },
      }),
    );
    await vi.waitFor(() => expect(dialog()).toBeNull());
  });

  it('replaces a section that exists with the whole new state, and deletes it on request', async () => {
    render();
    await press(rows()[3] as HTMLElement);
    expect(dialog()?.textContent).toContain('Edit the section');
    expect((field('Name') as HTMLInputElement).value).toBe('Preface');
    expect((field('First number') as HTMLInputElement).value).toBe('6');

    await type(field('Name'), 'Foreword');
    await press(button('Save the section'));
    expect(sdk.put).toHaveBeenCalledWith(
      expect.objectContaining({
        path: { project_id: PROJECT, section_id: 'preface' },
        body: expect.objectContaining({ first_page_id: 'p3', name: 'Foreword', start: 6 }),
      }),
    );
    await vi.waitFor(() => expect(dialog()).toBeNull());

    await press(rows()[3] as HTMLElement);
    await press(button('Delete the section'));
    expect(sdk.remove).toHaveBeenCalledWith(
      expect.objectContaining({ path: { project_id: PROJECT, section_id: 'preface' } }),
    );
  });

  it('offers no deletion for a section that is not made yet', async () => {
    render();
    await press(container.querySelector<HTMLElement>('[data-testid="section-add"]') as HTMLElement);

    expect(
      [...document.body.querySelectorAll('button')].some(
        (entry) => entry.textContent === 'Delete the section',
      ),
    ).toBe(false);
  });

  it('says why the server refused a section, and keeps the form open', async () => {
    sdk.create.mockRejectedValue(
      new ProblemError('Another section starts on that page.', HttpStatus.Conflict, null, []),
    );
    render({ selected: 'p3' });
    await press(container.querySelector<HTMLElement>('[data-testid="section-add"]') as HTMLElement);
    await press(button('Save the section'));

    await vi.waitFor(() =>
      expect(dialog()?.textContent).toContain('Another section starts on that page.'),
    );
    expect(dialog()).not.toBeNull();
  });
});
