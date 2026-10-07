import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PageVersionSchema } from '@/api';
import { version } from '@/features/processing/fixtures';
import { ResultNote } from '@/features/processing/ResultNote';
import { ProblemError } from '@/shared/http/problem';

/**
 * The notes of the reader on one result: the good and bad marks, the comment of one line or several, and what is sent to
 * the server when either changes.
 */

const sdk = vi.hoisted(() => ({
  mark: vi.fn(),
}));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  putMarkApiV1ProjectsProjectIdPagesPageIdVersionsVersionIdMarkPut: sdk.mark,
}));

const OLD = version('old', { mark: 'good' });
const NEW = version('new', { mark: 'bad' });

describe('ResultNote', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  /** Show the notes of the given results one after the other, as the rows of the timeline do. */
  async function render(...results: PageVersionSchema[]): Promise<void> {
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          {results.map((result) => (
            <ResultNote key={result.id} projectId="project" version={result} />
          ))}
        </QueryClientProvider>,
      );
    });
  }

  async function settle(): Promise<void> {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  const byId = (id: string, within: ParentNode = document): HTMLElement | null =>
    within.querySelector<HTMLElement>(`[data-testid="${id}"]`);

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    sdk.mark.mockReset();
    sdk.mark.mockResolvedValue({ data: {} });
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

  it('marks a result, which sends the mark with the comment it has, and takes the mark off again', async () => {
    await render(NEW);

    await act(async () => {
      byId('result-mark-good')?.click();
    });
    expect(sdk.mark.mock.calls[0]?.[0]).toMatchObject({
      path: { project_id: 'project', page_id: 'page', version_id: 'new' },
      body: { mark: 'good', comment: '' },
    });
    await settle();
    await settle();

    await act(async () => {
      byId('result-mark-bad')?.click();
    });
    expect(sdk.mark.mock.calls[1]?.[0]).toMatchObject({ body: { mark: null, comment: '' } });
  });

  it('edits the comment with a pencil button beside the marks, and shows the comment under them', async () => {
    await render(version('new', { comment: 'Slightly dark' }));

    const note = byId('result-note');
    const pencil = byId('result-comment-edit');
    expect(pencil?.querySelector('svg')).not.toBeNull();
    expect(pencil?.textContent).toBe('');
    expect(pencil?.getAttribute('aria-label')).toBe('Edit comment');
    expect(byId('result-comment')?.textContent).toBe('Slightly dark');
    expect(pencil?.parentElement?.compareDocumentPosition(byId('result-comment') as Node)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(note?.contains(pencil)).toBe(true);
  });

  it('labels the pencil as an addition while there is no comment', async () => {
    await render(NEW);

    expect(byId('result-comment-edit')?.getAttribute('aria-label')).toBe('Add a comment');
    expect(byId('result-comment')).toBeNull();
  });

  it('keeps the comment that is being written when the answer to a mark pressed meanwhile arrives', async () => {
    let answer: (value: unknown) => void = () => undefined;
    sdk.mark.mockReturnValue(
      new Promise((resolve) => {
        answer = resolve;
      }),
    );
    await render(NEW);
    await act(async () => {
      byId('result-comment-edit')?.click();
    });
    expect(byId('result-comment-input')).not.toBeNull();
    await act(async () => {
      byId('result-mark-good')?.click();
    });

    await act(async () => {
      answer({ data: {} });
    });
    await settle();

    expect(byId('result-comment-input')).not.toBeNull();
  });

  it('closes the field of the comment once the comment is saved', async () => {
    await render(NEW);
    await act(async () => {
      byId('result-comment-edit')?.click();
    });

    await act(async () => {
      byId('result-comment-save')?.click();
    });
    await settle();

    expect(sdk.mark.mock.calls[0]?.[0]).toMatchObject({ body: { mark: 'bad', comment: '' } });
    expect(byId('result-comment-input')).toBeNull();
  });

  it('sends the comment a result has along with the mark', async () => {
    await render(version('old', { comment: 'Too tight' }), NEW);

    await act(async () => {
      container
        .querySelector<HTMLElement>('[data-version="old"][data-testid="result-mark-good"]')
        ?.click();
    });

    expect(sdk.mark).toHaveBeenCalledTimes(1);
    expect(sdk.mark.mock.calls[0]?.[0]).toMatchObject({
      path: { project_id: 'project', page_id: 'page', version_id: 'old' },
      body: { mark: 'good', comment: 'Too tight' },
    });
  });

  it('shows the mark a result has as pressed, on that result only', async () => {
    await render(OLD, NEW);

    const pressed = (id: string, mark: 'good' | 'bad'): string | null | undefined =>
      container
        .querySelector(`[data-version="${id}"][data-testid="result-mark-${mark}"]`)
        ?.getAttribute('aria-pressed');
    expect([pressed('old', 'good'), pressed('old', 'bad')]).toEqual(['true', 'false']);
    expect([pressed('new', 'good'), pressed('new', 'bad')]).toEqual(['false', 'true']);
  });

  it('writes a comment of several lines and keeps the mark', async () => {
    await render(OLD, NEW);

    await act(async () => {
      container
        .querySelector<HTMLElement>('[data-version="old"][data-testid="result-comment-edit"]')
        ?.click();
    });
    const input = byId('result-comment-input') as HTMLTextAreaElement | null;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
      setter?.call(input, 'First try\nSecond try');
      input?.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await act(async () => {
      byId('result-comment-save')?.click();
    });

    expect(sdk.mark.mock.calls[0]?.[0]).toMatchObject({
      path: { version_id: 'old' },
      body: { mark: 'good', comment: 'First try\nSecond try' },
    });
  });

  it('tells the reader when the notes could not be saved', async () => {
    sdk.mark.mockRejectedValue(
      new ProblemError('Another job of this book is running.', 409, null, []),
    );
    await render(NEW);

    await act(async () => {
      byId('result-mark-good')?.click();
    });

    // The mutation reports its failure a few ticks after the click
    await vi.waitFor(() => expect(container.querySelector('[role="alert"]')).not.toBeNull());
  });
});
