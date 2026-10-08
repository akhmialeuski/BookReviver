import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { HistoryFrame } from '@/features/workspace/HistoryFrame';
import { HISTORY_OPEN_KEY } from '@/features/workspace/historyOpen';
import { StagePanel } from '@/features/workspace/StagePanel';

/**
 * The frame of the panel of a stage: the history of the open page is its last element, above the footer, on every stage,
 * with the content a stage passes or else grey with the reason that the stage keeps none.
 */

describe('StagePanel', () => {
  let container: HTMLDivElement;
  let root: Root;

  async function render(element: React.JSX.Element): Promise<void> {
    await act(async () => {
      root.render(element);
    });
  }

  const find = (testId: string): HTMLElement | null =>
    container.querySelector<HTMLElement>(`[data-testid="${testId}"]`);

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    localStorage.clear();
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it('ends the scrolling area with the history, after the body, and keeps the footer below the area', async () => {
    await render(
      <StagePanel stage="geometry" available footer={<button type="button">Run</button>}>
        <p data-testid="body">Body</p>
      </StagePanel>,
    );

    const area = find('stage-panel-scroll');
    expect(area?.lastElementChild).toBe(find('page-history'));
    expect(area?.firstElementChild).toBe(find('body'));
    expect(area?.contains(find('page-history'))).toBe(true);
    expect(find('stage-panel')?.querySelector('footer')?.textContent).toBe('Run');
    expect(find('stage-panel')?.lastElementChild?.tagName).toBe('FOOTER');
  });

  it('draws the frame grey and shut with the reason when the stage gives no content', async () => {
    localStorage.setItem(HISTORY_OPEN_KEY, 'open');
    await render(
      <StagePanel stage="import" available>
        <p>Body</p>
      </StagePanel>,
    );

    const history = find('page-history');
    expect(history?.getAttribute('aria-disabled')).toBe('true');
    expect(history?.className).toContain('opacity-60');
    expect(find('page-history-toggle')?.hasAttribute('disabled')).toBe(true);
    expect(find('page-history-reason')?.textContent).toBe(
      'This stage keeps no history of its pages.',
    );
    expect(find('page-history-count')).toBeNull();
    expect(history?.textContent).toContain('History of this page');
  });

  it('draws the frame on a stage that is not available yet, with no body and no footer', async () => {
    await render(<StagePanel stage="geometry" available={false} />);

    expect(find('stage-panel-scroll')?.children).toHaveLength(1);
    expect(find('page-history')?.getAttribute('aria-disabled')).toBe('true');
    expect(find('stage-panel')?.querySelector('footer')).toBeNull();
    expect(find('stage-panel')?.textContent).toContain('Soon');
  });

  it('draws the content a stage passes in place of the grey frame, as the last element too', async () => {
    await render(
      <StagePanel
        stage="geometry"
        available
        history={
          <HistoryFrame count={4} reason={null}>
            <p data-testid="events">Events</p>
          </HistoryFrame>
        }
      >
        <p>Body</p>
      </StagePanel>,
    );

    const histories = container.querySelectorAll('[data-testid="page-history"]');
    expect(histories).toHaveLength(1);
    expect(find('stage-panel-scroll')?.lastElementChild).toBe(histories[0]);
    expect(find('page-history')?.getAttribute('aria-disabled')).toBe('false');
    expect(find('page-history-count')?.textContent).toBe('4 events');
    expect(find('page-history-reason')).toBeNull();
  });

  it('lets the grids of the body and of the footer shrink to the panel, so no grid sets a column of its own', async () => {
    await render(
      <StagePanel stage="geometry" available footer={<button type="button">Run</button>} />,
    );

    // Tailwind writes this variant as `.panel .grid > *`, and a zero minimum width lets an auto column shrink
    expect(find('stage-panel')?.className).toContain('[&_.grid>*]:min-w-0');
    expect(find('stage-panel')?.querySelector('footer')?.parentElement).toBe(find('stage-panel'));
  });
});

describe('HistoryFrame', () => {
  let container: HTMLDivElement;
  let root: Root;

  async function render(element: React.JSX.Element): Promise<void> {
    await act(async () => {
      root.render(element);
    });
  }

  const find = (testId: string): HTMLElement | null =>
    container.querySelector<HTMLElement>(`[data-testid="${testId}"]`);

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    localStorage.clear();
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  const frame = (count: number | null, reason: string | null = null): React.JSX.Element => (
    <HistoryFrame
      count={count}
      reason={reason}
      filters={<div data-testid="filters">Filters</div>}
      notice={<p data-testid="notice">Notice</p>}
    >
      <p data-testid="events">Events</p>
    </HistoryFrame>
  );

  it('is collapsed by default with the title and the number of events, and no button on the header', async () => {
    await render(frame(7));

    expect(find('page-history')?.textContent).toContain('History of this page');
    expect(find('page-history-count')?.textContent).toBe('7 events');
    expect(find('page-history-toggle')?.querySelectorAll('button')).toHaveLength(0);
    expect(find('events')).toBeNull();
    expect(find('filters')).toBeNull();
    expect(find('notice')).not.toBeNull();
  });

  it('counts one event in the singular, and leaves the chip out while the count is not known', async () => {
    await render(frame(1));
    expect(find('page-history-count')?.textContent).toBe('1 event');

    await render(frame(null));
    expect(find('page-history-count')).toBeNull();
    expect(find('page-history')?.getAttribute('aria-disabled')).toBe('false');
  });

  it('opens on the header, shows the filters above the events, and remembers the choice for the next one', async () => {
    await render(frame(2));
    await act(async () => find('page-history-toggle')?.click());

    expect(find('events')).not.toBeNull();
    expect(find('filters')?.compareDocumentPosition(find('events') as Node)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(localStorage.getItem(HISTORY_OPEN_KEY)).toBe('open');

    act(() => root.unmount());
    root = createRoot(container);
    await render(frame(2));
    expect(find('events')).not.toBeNull();

    await act(async () => find('page-history-toggle')?.click());
    expect(find('events')).toBeNull();
    expect(localStorage.getItem(HISTORY_OPEN_KEY)).toBe('collapsed');
  });

  it('is grey with its reason and stays shut when it has one, though the reader chose to open it', async () => {
    localStorage.setItem(HISTORY_OPEN_KEY, 'open');
    await render(frame(3, 'No page is chosen.'));

    expect(find('page-history')?.getAttribute('aria-disabled')).toBe('true');
    expect(find('page-history-toggle')?.hasAttribute('disabled')).toBe(true);
    expect(find('page-history-reason')?.textContent).toBe('No page is chosen.');
    expect(find('page-history-count')).toBeNull();
    expect(find('events')).toBeNull();
    expect(find('notice')).not.toBeNull();
  });
});
