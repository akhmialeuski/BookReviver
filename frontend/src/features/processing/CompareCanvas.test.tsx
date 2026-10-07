import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CompareCanvas } from '@/features/processing/CompareCanvas';
import {
  type ComparePair,
  type PairPlacement,
  Side,
  SourceKind,
} from '@/features/processing/compare';
import { CompareMode } from '@/features/workspace/params';

/**
 * The canvas of the compare around its OpenSeadragon stage: what it puts on the stage and when, what it draws over it for
 * each mode, and what Space and the keys of the divider do.
 *
 * OpenSeadragon needs a canvas that jsdom does not have, so the stage is a stand-in that records what it is told. Its
 * pictures and its clip are checked in a real browser by the end-to-end scenario.
 */

const stage = vi.hoisted(() => ({
  instances: [] as unknown[],
  show: vi.fn(),
  setMode: vi.fn(),
  setDivider: vi.fn(),
  setHolding: vi.fn(),
  setPadding: vi.fn(),
  setReach: vi.fn(),
  destroy: vi.fn(),
  viewer: { name: 'viewer' },
  image: { name: 'image' } as { name: string } | null,
}));

vi.mock('@/features/processing/compareStage', () => ({
  CompareStage: class {
    constructor() {
      stage.instances.push(this);
    }
    show = stage.show;
    setMode = stage.setMode;
    setDivider = stage.setDivider;
    setHolding = stage.setHolding;
    setPadding = stage.setPadding;
    setReach = stage.setReach;
    destroy = stage.destroy;
    viewer = stage.viewer;
    get image() {
      return stage.image;
    }
    fit = vi.fn();
    zoomIn = vi.fn();
    zoomOut = vi.fn();
  },
}));

const BEFORE = { kind: SourceKind.Iiif, url: '/before/info.json' } as const;
const AFTER = { kind: SourceKind.Image, url: '/after.png' } as const;

describe('CompareCanvas', () => {
  let container: HTMLDivElement;
  let root: Root;

  function render(
    mode: CompareMode,
    pairs: ComparePair = { before: BEFORE, after: AFTER },
    notice: { text: string; working: boolean } | null = null,
    editor: Pick<
      React.ComponentProps<typeof CompareCanvas>,
      'overlay' | 'roomShare' | 'reach' | 'placement'
    > = {},
  ): void {
    act(() =>
      root.render(
        <CompareCanvas
          pairs={pairs}
          mode={mode}
          beforeLabel="Before · result of Order"
          afterLabel="After · Geometry"
          notice={notice}
          pageIds={['p1']}
          {...editor}
        />,
      ),
    );
  }

  const handle = (): HTMLElement | null =>
    container.querySelector<HTMLElement>('[data-testid="compare-handle"]');

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    stage.instances.length = 0;
    stage.show.mockReset();
    stage.show.mockResolvedValue({ failed: [], loaded: [] });
    stage.setMode.mockReset();
    stage.setDivider.mockReset();
    stage.setHolding.mockReset();
    stage.setPadding.mockReset();
    stage.setReach.mockReset();
    stage.destroy.mockReset();
    stage.image = { name: 'image' };
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  it('puts the two pictures on the stage once, and says when they are loaded', async () => {
    render(CompareMode.Off);
    await act(async () => {
      await Promise.resolve();
    });

    expect(stage.instances).toHaveLength(1);
    expect(stage.show).toHaveBeenCalledTimes(1);
    expect(stage.show).toHaveBeenCalledWith(BEFORE, AFTER, 'p1', null);
    expect(
      container.querySelector('[data-testid="viewer-canvas"]')?.getAttribute('data-state'),
    ).toBe('ready');
  });

  it('hands the place of the two pictures to the stage, and shows them again only when the place changes', async () => {
    const place: PairPlacement = {
      base: Side.After,
      left: 0.1,
      top: 0.05,
      width: 0.8,
      height: 0.9,
    };
    render(CompareMode.Swipe, undefined, null, { placement: place });
    await act(async () => {
      await Promise.resolve();
    });
    expect(stage.show).toHaveBeenLastCalledWith(BEFORE, AFTER, 'p1', place);

    // The same numbers in a new object are the same place
    render(CompareMode.Swipe, undefined, null, { placement: { ...place } });
    expect(stage.show).toHaveBeenCalledTimes(1);

    render(CompareMode.Swipe, undefined, null, { placement: { ...place, left: 0.2 } });
    expect(stage.show).toHaveBeenCalledTimes(2);
  });

  it('draws an editor over the picture once it is loaded, with the viewer and the picture it lies on', async () => {
    const overlay = vi.fn((scene: unknown) => (
      <p data-testid="editor-stand-in">{JSON.stringify(scene)}</p>
    ));
    render(CompareMode.Off, { before: null, after: AFTER }, null, { overlay, roomShare: 0.08 });
    expect(container.querySelector('[data-testid="editor-stand-in"]')).toBeNull();

    await act(async () => {
      await Promise.resolve();
    });

    expect(container.querySelector('[data-testid="editor-stand-in"]')?.textContent).toBe(
      JSON.stringify({ viewer: stage.viewer, image: stage.image }),
    );
    expect(stage.setPadding).toHaveBeenLastCalledWith(0.08);
  });

  it('hands what the editor draws beyond the page to the stage, and nothing when there is none', async () => {
    const reach = { rect: { left: -10, top: -20, width: 120, height: 240 }, size: null };
    render(CompareMode.Off, { before: null, after: AFTER }, null, { reach });
    await act(async () => {
      await Promise.resolve();
    });

    expect(stage.setReach).toHaveBeenLastCalledWith(reach);

    render(CompareMode.Off, { before: null, after: AFTER }, null, { reach: null });
    await act(async () => {
      await Promise.resolve();
    });

    expect(stage.setReach).toHaveBeenLastCalledWith(null);
  });

  it('draws no editor when none is passed in, and leaves no room round the page', async () => {
    render(CompareMode.Off);
    await act(async () => {
      await Promise.resolve();
    });

    expect(container.querySelector('[data-testid="editor-stand-in"]')).toBeNull();
    expect(stage.setPadding).toHaveBeenLastCalledWith(0);
  });

  it('does not load the pictures again when only the mode changes', async () => {
    render(CompareMode.Off);
    render(CompareMode.Swipe);
    render(CompareMode.Side);
    await act(async () => {
      await Promise.resolve();
    });

    expect(stage.show).toHaveBeenCalledTimes(1);
    expect(stage.setMode).toHaveBeenLastCalledWith(CompareMode.Side);
  });

  it('loads the pictures again for another page', async () => {
    render(CompareMode.Swipe);
    render(CompareMode.Swipe, {
      before: BEFORE,
      after: { kind: SourceKind.Iiif, url: '/other/info.json' },
    });

    expect(stage.show).toHaveBeenCalledTimes(2);
  });

  it('says a picture could not be loaded', async () => {
    stage.show.mockResolvedValue({ failed: ['after'], loaded: [] });
    render(CompareMode.Off);
    await act(async () => {
      await Promise.resolve();
    });

    expect(
      container.querySelector('[data-testid="viewer-canvas"]')?.getAttribute('data-state'),
    ).toBe('failed');
    expect(container.textContent).toContain('could not be loaded');
  });

  it('draws the divider and both labels for a swipe, and no divider for the other modes', () => {
    render(CompareMode.Swipe);
    expect(handle()).not.toBeNull();
    expect(container.textContent).toContain('Before · result of Order');
    expect(container.textContent).toContain('After · Geometry');

    render(CompareMode.Off);
    expect(handle()).toBeNull();
    expect(container.textContent).not.toContain('Before · result of Order');

    render(CompareMode.Side);
    expect(handle()).toBeNull();
    expect(container.textContent).toContain('Before · result of Order');
    expect(container.querySelector('[data-testid="viewer-canvas-after"]')?.className).not.toContain(
      'hidden',
    );
  });

  it('draws no divider when there is no picture before to swipe over', () => {
    render(CompareMode.Swipe, { before: null, after: AFTER });

    expect(handle()).toBeNull();
  });

  it('moves the divider with the arrow keys, within the canvas', () => {
    render(CompareMode.Swipe);

    act(() => {
      handle()?.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    });

    expect(handle()?.getAttribute('aria-valuenow')).toBe('55');
    expect(stage.setDivider).toHaveBeenLastCalledWith(0.55);

    for (let press = 0; press < 20; press += 1) {
      act(() => {
        handle()?.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
      });
    }
    expect(handle()?.getAttribute('aria-valuenow')).toBe('98');
  });

  it('shows the picture before while Space is held, and the picture after again when it is let go', () => {
    render(CompareMode.Off);

    act(() => {
      document.body.dispatchEvent(
        new KeyboardEvent('keydown', { code: 'Space', key: ' ', bubbles: true, cancelable: true }),
      );
    });
    expect(stage.setHolding).toHaveBeenLastCalledWith(true);
    expect(
      container.querySelector('[data-testid="viewer-canvas"]')?.getAttribute('data-holding'),
    ).toBe('true');
    expect(container.textContent).toContain('Before · result of Order');

    act(() => {
      document.body.dispatchEvent(
        new KeyboardEvent('keyup', { code: 'Space', key: ' ', bubbles: true }),
      );
    });
    expect(stage.setHolding).toHaveBeenLastCalledWith(false);
  });

  it('leaves Space alone when there is no picture before to show', () => {
    render(CompareMode.Off, { before: null, after: AFTER });

    act(() => {
      document.body.dispatchEvent(
        new KeyboardEvent('keydown', { code: 'Space', key: ' ', bubbles: true, cancelable: true }),
      );
    });

    expect(stage.setHolding).not.toHaveBeenCalledWith(true);
  });

  it('says what the preview is doing, and why it failed', () => {
    render(CompareMode.Swipe, undefined, { text: 'Making the preview…', working: true });
    expect(container.querySelector('[data-testid="preview-working"]')?.textContent).toBe(
      'Making the preview…',
    );

    render(CompareMode.Swipe, undefined, {
      text: 'The preview could not be made.',
      working: false,
    });
    expect(container.querySelector('[data-testid="preview-error"]')?.textContent).toBe(
      'The preview could not be made.',
    );
    expect(container.querySelector('[data-testid="preview-working"]')).toBeNull();
  });

  it('releases the stage when it leaves', () => {
    render(CompareMode.Off);

    act(() => root.unmount());

    expect(stage.destroy).toHaveBeenCalled();
    root = createRoot(container);
  });
});
