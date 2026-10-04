import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MarginsPanel } from '@/features/editors/MarginsPanel';
import { type StepSettings, StepSettingsContext } from '@/features/editors/stepSettings';

/** The part of the Margins editor in the panel: the alignment of the box on the page. */

describe('MarginsPanel', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  function render(values: Record<string, unknown>, busy = false) {
    const set = vi.fn();
    const settings: StepSettings = { values, set, busy };
    act(() =>
      root.render(
        <StepSettingsContext.Provider value={settings}>
          <MarginsPanel />
        </StepSettingsContext.Provider>,
      ),
    );
    return set;
  }

  const radio = (group: string, value: string): HTMLInputElement | null =>
    container.querySelector<HTMLInputElement>(
      `[data-testid="${group}"] input[type="radio"][value="${value}"]`,
    );

  it('says how the box and the border are moved', () => {
    render({});

    expect(container.querySelector('[data-testid="margins-hint"]')?.textContent).toContain(
      'outer border',
    );
  });

  it('shows the alignment the page runs with', () => {
    render({ align_vertical: 'bottom', align_horizontal: 'outer', margins_by: 'inner-outer' });

    expect(radio('align-vertical', 'bottom')?.checked).toBe(true);
    expect(radio('align-horizontal', 'outer')?.checked).toBe(true);
  });

  it('starts from the top and the middle, as the step does', () => {
    render({});

    expect(radio('align-vertical', 'top')?.checked).toBe(true);
    expect(radio('align-horizontal', 'center')?.checked).toBe(true);
  });

  it('offers left and right when the margins are the same on every page', () => {
    render({ margins_by: 'left-right', align_horizontal: 'left' });

    expect(radio('align-horizontal', 'left')?.checked).toBe(true);
    expect(radio('align-horizontal', 'inner')).toBeNull();
  });

  it('sets the alignment of the open page when a choice is made', () => {
    const set = render({ margins_by: 'inner-outer' });

    act(() => radio('align-vertical', 'center')?.click());
    act(() => radio('align-horizontal', 'inner')?.click());

    expect(set).toHaveBeenNthCalledWith(1, 'align_vertical', 'center');
    expect(set).toHaveBeenNthCalledWith(2, 'align_horizontal', 'inner');
  });

  it('cannot be changed while a change is being saved', () => {
    render({}, true);

    expect(radio('align-vertical', 'center')?.disabled).toBe(true);
  });
});
