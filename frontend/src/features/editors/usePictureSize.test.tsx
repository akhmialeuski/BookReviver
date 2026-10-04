import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Size } from '@/features/editors/shapes';
import { usePictureSize } from '@/features/editors/usePictureSize';
import { type ImageSource, SourceKind } from '@/features/processing/compare';

/** The size of a picture, read from the image information document of its pyramid and from nothing else. */

const PYRAMID: ImageSource = { kind: SourceKind.Iiif, url: '/page/info.json' };
const PLAIN: ImageSource = { kind: SourceKind.Image, url: '/page/preview.jpg' };

describe('usePictureSize', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;
  let size: Size | null;
  const fetched = vi.fn();

  function Harness({
    source,
    enabled,
  }: {
    source: ImageSource | null;
    enabled: boolean;
  }): React.JSX.Element {
    size = usePictureSize(source, enabled);
    return <span />;
  }

  async function render(source: ImageSource | null, enabled = true): Promise<void> {
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <Harness source={source} enabled={enabled} />
        </QueryClientProvider>,
      );
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    fetched.mockReset();
    fetched.mockResolvedValue({ ok: true, json: async () => ({ width: 1200, height: 1800 }) });
    vi.stubGlobal('fetch', fetched);
    size = null;
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

  it('reads the width and the height from the information document of the pyramid', async () => {
    await render(PYRAMID);

    expect(fetched).toHaveBeenCalledWith('/page/info.json');
    expect(size).toEqual({ width: 1200, height: 1800 });
  });

  it('asks for nothing when the size is not wanted, when there is no picture, or when it is a plain image', async () => {
    await render(PYRAMID, false);
    await render(null);
    await render(PLAIN);

    expect(fetched).not.toHaveBeenCalled();
    expect(size).toBeNull();
  });

  it('gives no size when the document is not there or is not what a pyramid says', async () => {
    fetched.mockResolvedValue({ ok: false, json: async () => ({}) });
    await render(PYRAMID);
    expect(size).toBeNull();

    client.clear();
    fetched.mockResolvedValue({ ok: true, json: async () => ({ width: 'wide' }) });
    await render({ kind: SourceKind.Iiif, url: '/other/info.json' });
    expect(size).toBeNull();
  });

  it('reads a pyramid once, whoever asks', async () => {
    await render(PYRAMID);
    await render(PYRAMID);

    expect(fetched).toHaveBeenCalledTimes(1);
  });
});
