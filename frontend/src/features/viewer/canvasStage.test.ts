import type OpenSeadragon from 'openseadragon';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CanvasStage } from '@/features/viewer/canvasStage';

/**
 * How the stages put pictures on an OpenSeadragon viewer when some of them cannot be read.
 *
 * The queue in which OpenSeadragon adds pictures is the thing under test, so the viewer is the real one. Only what jsdom
 * lacks is replaced: the canvas context, `matchMedia`, and the requests for an `info.json`, which answer after a delay
 * that the test chooses, so that a picture can be read before or after another.
 */

const FAST_MS = 5;
const SLOW_MS = 60;
const GIVE_UP_MS = 1000;

const INFO = JSON.stringify({
  '@context': 'http://iiif.io/api/image/3/context.json',
  id: 'http://pages/ok',
  type: 'ImageService3',
  protocol: 'http://iiif.io/api/image',
  profile: 'level0',
  width: 100,
  height: 100,
  tiles: [{ width: 256, scaleFactors: [1] }],
});

/** Answers 404 for an address with `missing` in it, after a delay that `slow` in the address makes longer. */
class FakeRequest {
  status = 0;
  responseText = '';
  readyState = 0;
  onreadystatechange: (() => void) | null = null;
  private url = '';

  open(_method: string, url: string): void {
    this.url = url;
  }

  setRequestHeader(): void {}

  getResponseHeader(): string {
    return 'application/json';
  }

  send(): void {
    const missing = this.url.includes('missing');
    setTimeout(
      () => {
        this.status = missing ? 404 : 200;
        this.responseText = missing ? '' : INFO;
        this.readyState = 4;
        this.onreadystatechange?.();
      },
      this.url.includes('slow') ? SLOW_MS : FAST_MS,
    );
  }
}

/** A stage with nothing of its own, which lets the test ask for pictures. */
class Probe extends CanvasStage {
  protected override fittedSize(): null {
    return null;
  }

  add(tileSource: string): Promise<OpenSeadragon.TiledImage | null> {
    return this.loadPicture(this.viewer, tileSource);
  }
}

/** What a picture came to: `item` for one that was read, `null` for one that was not, `pending` after too long a wait. */
async function outcome(picture: Promise<unknown>): Promise<string> {
  const gaveUp = new Promise<string>((resolve) => setTimeout(() => resolve('pending'), GIVE_UP_MS));
  return Promise.race([picture.then((item) => (item === null ? 'null' : 'item')), gaveUp]);
}

describe('CanvasStage.loadPicture', () => {
  let stage: Probe;

  beforeEach(() => {
    vi.stubGlobal('XMLHttpRequest', FakeRequest);
    vi.stubGlobal('matchMedia', () => ({
      matches: false,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    }));
    const context = new Proxy({}, { get: () => () => ({}) });
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(
      context as unknown as CanvasRenderingContext2D,
    );
    const element = document.createElement('div');
    document.body.append(element);
    stage = new Probe(element);
  });

  afterEach(() => {
    if (!stage.viewer.isDestroyed()) {
      stage.destroy();
    }
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('gives null for a picture that answers 404', async () => {
    expect(await outcome(stage.add('http://pages/missing/info.json'))).toBe('null');
  });

  it('settles a picture that is read before the one asked for ahead of it fails', async () => {
    // The 404 comes later than the other answer, as when the server is slower for a pyramid that is not cut
    const failing = stage.add('http://pages/missing-slow/info.json');
    const reading = stage.add('http://pages/ok/info.json');

    expect([await outcome(failing), await outcome(reading)]).toEqual(['null', 'item']);
  });

  it('gives null for a picture asked of a viewer that is gone', async () => {
    stage.destroy();

    expect(await outcome(stage.add('http://pages/ok/info.json'))).toBe('null');
  });
});
