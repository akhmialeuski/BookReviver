import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { HistoryFrame } from '@/features/workspace/HistoryFrame';
import { HISTORY_OPEN_KEY } from '@/features/workspace/historyOpen';
import { StagePanel } from '@/features/workspace/StagePanel';

/**
 * The layout of the panel of a stage: the slots in one fixed order, an empty slot left out, every setting in one frame the
 * layout draws, the facts last in the page section, and the history of the open page as the last element, above the
 * footer, on every stage, with the content a stage passes or else grey with the reason that the stage keeps none.
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

  const FULL = {
    recipe: <p data-testid="recipe-content">Recipe</p>,
    step: { title: 'Deskew', stepId: 's1', children: <p data-testid="step-note">Off</p> },
    settings: <p data-testid="settings-content">Settings</p>,
    page: {
      title: 'This page · 14',
      children: <p data-testid="page-content">State</p>,
      facts: <p data-testid="facts-content">Facts</p>,
    },
    footer: <button type="button">Run</button>,
  };

  it('draws the slots in one fixed order, the history last in the scrolling area and the footer below it', async () => {
    await render(<StagePanel stage="geometry" available {...FULL} />);

    const area = find('stage-panel-scroll');
    expect([...(area?.children ?? [])].map((child) => child.getAttribute('data-testid'))).toEqual([
      'panel-recipe',
      'panel-step',
      'panel-settings',
      'panel-page',
      'page-history',
    ]);
    expect(find('stage-panel')?.querySelector('footer')?.textContent).toBe('Run');
    expect(find('stage-panel')?.lastElementChild?.tagName).toBe('FOOTER');
  });

  it('draws nothing for a slot that is empty, and the grey history alone for a stage with no slot filled', async () => {
    await render(<StagePanel stage="import" available settings={null} footer={false} />);

    expect(find('stage-panel-scroll')?.children).toHaveLength(1);
    expect(find('stage-panel-scroll')?.firstElementChild).toBe(find('page-history'));
    expect(find('stage-panel')?.querySelector('footer')).toBeNull();

    await render(<StagePanel stage="geometry" available settings={<p>Settings</p>} />);
    expect(find('panel-recipe')).toBeNull();
    expect(find('panel-step')).toBeNull();
    expect(find('panel-page')).toBeNull();
    expect(find('panel-settings')).not.toBeNull();
  });

  it('puts every setting in one bordered frame that the layout draws, whatever the stage passes', async () => {
    await render(
      <StagePanel
        stage="geometry"
        available
        settings={
          <>
            <p data-testid="form">Form</p>
            <p data-testid="measure">Measure</p>
          </>
        }
      />,
    );

    const frame = find('panel-settings');
    expect(frame?.className).toContain('rounded-lg');
    expect(frame?.className).toContain('border');
    expect(frame?.contains(find('form'))).toBe(true);
    expect(frame?.contains(find('measure'))).toBe(true);
    expect(container.querySelectorAll('[data-testid="panel-settings"]')).toHaveLength(1);
  });

  it('names the open step by its title alone and keeps the notes about it under the title', async () => {
    await render(<StagePanel stage="geometry" available step={FULL.step} />);

    expect(find('step-panel-title')?.textContent).toBe('Deskew');
    expect(find('panel-step')?.getAttribute('data-step-id')).toBe('s1');
    expect(find('panel-step')?.contains(find('step-note'))).toBe(true);
  });

  it('draws one section for the page with its facts last, right above the history', async () => {
    await render(<StagePanel stage="geometry" available page={FULL.page} />);

    const section = find('panel-page');
    expect(section?.querySelector('h3')?.textContent).toBe('This page · 14');
    expect(section?.lastElementChild).toBe(find('panel-facts'));
    expect(find('panel-facts')?.contains(find('facts-content'))).toBe(true);
    expect(section?.firstElementChild?.nextElementSibling).toBe(find('page-content'));
    expect(section?.nextElementSibling).toBe(find('page-history'));
    expect(container.textContent?.match(/This page/g)).toHaveLength(1);
  });

  it('draws no facts wrapper for a page with no facts, and the action beside the heading', async () => {
    await render(
      <StagePanel
        stage="geometry"
        available
        page={{ title: 'Selected pages', action: <button type="button">Clear</button> }}
      />,
    );

    expect(find('panel-facts')).toBeNull();
    expect(find('panel-page')?.querySelector('button')?.textContent).toBe('Clear');
  });

  it('draws every section heading in the one style of the shared heading', async () => {
    await render(<StagePanel stage="geometry" available {...FULL} />);

    const headings = [
      ...container.querySelectorAll(
        '[data-testid="panel-recipe"] h3, [data-testid="panel-page"] h3, [data-testid="page-history"] h3',
      ),
    ];
    expect(headings).toHaveLength(3);
    for (const heading of headings) {
      expect(heading.className).toContain('uppercase');
      expect(heading.className).toContain('tracking-wide');
    }
  });

  it('draws the frame grey and shut with the reason when the stage gives no history', async () => {
    localStorage.setItem(HISTORY_OPEN_KEY, 'open');
    await render(<StagePanel stage="import" available settings={<p>Body</p>} />);

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

  it('draws the frame on a stage that is not available yet, with no slot and no footer', async () => {
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
        settings={<p>Body</p>}
        history={
          <HistoryFrame count={4} reason={null}>
            <p data-testid="events">Events</p>
          </HistoryFrame>
        }
      />,
    );

    const histories = container.querySelectorAll('[data-testid="page-history"]');
    expect(histories).toHaveLength(1);
    expect(find('stage-panel-scroll')?.lastElementChild).toBe(histories[0]);
    expect(find('page-history')?.getAttribute('aria-disabled')).toBe('false');
    expect(find('page-history-count')?.textContent).toBe('4 events');
    expect(find('page-history-reason')).toBeNull();
  });

  it('lets the grids of the slots and of the footer shrink to the panel, so no grid sets a column of its own', async () => {
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
