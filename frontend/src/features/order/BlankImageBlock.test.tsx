import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PageSchema } from '@/api';
import { BlankImageBlock } from '@/features/order/BlankImageBlock';
import { page } from '@/features/workspace/fixtures';
import { ProblemError } from '@/shared/http/problem';

/**
 * The choice of the image of blank pages: the radio of the choice the selected pages share, and the button that writes
 * the chosen image to every blank page of the book.
 *
 * The generated client is replaced by a function the test reads, so the request is seen as the server gets it.
 */

const sdk = vi.hoisted(() => ({ fill: vi.fn() }));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  fillBlankPagesApiV1ProjectsProjectIdPagesBlankFillPost: sdk.fill,
}));

const PROJECT = 'book';
const BLANK_PAGES = [
  page('a', { kind: 'blank', blank_fill: 'white' }),
  page('b', { kind: 'blank', blank_fill: 'scan' }),
  page('c', { kind: 'blank', blank_fill: 'scan' }),
];

describe('BlankImageBlock', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  function render(selected: readonly PageSchema[]): void {
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <BlankImageBlock projectId={PROJECT} selected={selected} blankPages={BLANK_PAGES} />
        </QueryClientProvider>,
      ),
    );
  }

  const radio = (label: string): HTMLInputElement => {
    const field = [...container.querySelectorAll<HTMLInputElement>('input[type="radio"]')].find(
      (input) => container.querySelector(`label[for="${input.id}"]`)?.textContent === label,
    );
    if (field === undefined) {
      throw new Error(`No choice is called ${label}.`);
    }
    return field;
  };

  const applyAll = (): HTMLButtonElement => {
    const button = [...container.querySelectorAll('button')].find((candidate) =>
      candidate.textContent?.startsWith('Apply to all'),
    );
    if (button === undefined) {
      throw new Error('The block has no button for all the blank pages.');
    }
    return button;
  };

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    sdk.fill.mockReset();
    sdk.fill.mockResolvedValue({ data: undefined });
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

  it('checks the choice the selected pages share', () => {
    render([BLANK_PAGES[0] as PageSchema]);
    expect(radio('White leaf').checked).toBe(true);
    expect(radio('Keep the scan').checked).toBe(false);
  });

  it('checks no choice when the selected pages differ', () => {
    render(BLANK_PAGES);
    expect(radio('White leaf').checked).toBe(false);
    expect(radio('Keep the scan').checked).toBe(false);
    expect(radio('Paper of the book').checked).toBe(false);
    expect(applyAll().disabled).toBe(true);
  });

  it('writes a choice to every selected page in one request', async () => {
    render([BLANK_PAGES[1] as PageSchema, BLANK_PAGES[2] as PageSchema]);

    await act(async () => radio('Paper of the book').click());

    await vi.waitFor(() => expect(sdk.fill).toHaveBeenCalledTimes(1));
    expect(sdk.fill).toHaveBeenCalledWith(
      expect.objectContaining({
        path: { project_id: PROJECT },
        body: { page_ids: ['b', 'c'], blank_fill: 'paper' },
      }),
    );
  });

  it('names the number of blank pages on the button and writes the chosen image to all of them', async () => {
    render([BLANK_PAGES[0] as PageSchema]);
    expect(applyAll().textContent).toBe('Apply to all 3 Blank pages');

    await act(async () => applyAll().click());

    await vi.waitFor(() => expect(sdk.fill).toHaveBeenCalledTimes(1));
    expect(sdk.fill).toHaveBeenCalledWith(
      expect.objectContaining({ body: { page_ids: ['a', 'b', 'c'], blank_fill: 'white' } }),
    );
  });

  it('shows no error while the pages are written', async () => {
    render([BLANK_PAGES[1] as PageSchema]);

    await act(async () => radio('Paper of the book').click());

    await vi.waitFor(() => expect(sdk.fill).toHaveBeenCalledTimes(1));
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });

  it('shows the answer of the server in the block when the request is rejected', async () => {
    sdk.fill.mockRejectedValue(new ProblemError('The leaf could not be drawn.', 500, null, []));
    render([BLANK_PAGES[1] as PageSchema, BLANK_PAGES[2] as PageSchema]);

    await act(async () => radio('Paper of the book').click());

    await vi.waitFor(() =>
      expect(
        container.querySelector('[data-testid="blank-leaf"] [role="alert"]')?.textContent,
      ).toBe('The leaf could not be drawn.'),
    );
  });
});
