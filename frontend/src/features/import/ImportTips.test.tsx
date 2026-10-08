import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ImportTips } from '@/features/import/EmptyImport';

/** The panel of a book without files: what is good to know before the first upload, in the page section of the layout. */

describe('ImportTips', () => {
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

  it('puts the tips in the one page section under their title, with no frame of settings', () => {
    act(() => root.render(<ImportTips />));

    const section = container.querySelector('[data-testid="panel-page"]');
    expect(section?.querySelector('h3')?.textContent).toBe('Good to know');
    expect(section?.contains(container.querySelector('[data-testid="import-tips"]'))).toBe(true);
    expect(container.querySelector('[data-testid="import-tips"]')?.children.length).toBeGreaterThan(
      0,
    );
    expect(container.querySelector('[data-testid="panel-settings"]')).toBeNull();
    expect(container.querySelector('footer')).toBeNull();
  });

  it('keeps the history of the panel grey, since the stage keeps none', () => {
    act(() => root.render(<ImportTips />));

    expect(
      container.querySelector('[data-testid="page-history"]')?.getAttribute('aria-disabled'),
    ).toBe('true');
  });
});
