import { afterEach, describe, expect, it, vi } from 'vitest';
import { extendStroke, paintMask, radiusOf, startStroke } from '@/features/editors/brush';
import { readBrush, writeBrush } from '@/features/editors/shapes';

describe('radiusOf', () => {
  it('is half of a share of the width of the page, so the brush covers the same part at any resolution', () => {
    expect(radiusOf(2, { width: 1000, height: 1500 })).toBe(10);
    expect(radiusOf(2, { width: 4000, height: 6000 })).toBe(40);
  });
});

describe('startStroke and extendStroke', () => {
  it('begins a stroke at a point and adds the next points to the last stroke only', () => {
    const first = { strokes: [startStroke({ x: 1, y: 2 }, 5)] };
    const two = { strokes: [...first.strokes, startStroke({ x: 9, y: 9 }, 5)] };
    const grown = extendStroke(two, { x: 10, y: 11 });

    expect(grown.strokes[0]).toBe(first.strokes[0]);
    expect(grown.strokes[1]?.points).toEqual([
      { x: 9, y: 9 },
      { x: 10, y: 11 },
    ]);
  });

  it('adds nothing when there is no stroke to extend', () => {
    const none = { strokes: [] };

    expect(extendStroke(none, { x: 1, y: 1 })).toBe(none);
  });
});

describe('the strokes as the server stores them', () => {
  it('survive a write and a read', () => {
    const brush = {
      strokes: [
        { radius: 12, points: [{ x: 1, y: 2 }] },
        {
          radius: 4,
          points: [
            { x: 3, y: 4 },
            { x: 5, y: 6 },
          ],
        },
      ],
    };

    expect(readBrush(writeBrush(brush))).toEqual(brush);
  });

  it.each([
    ['no list', {}],
    ['a stroke with no radius', { strokes: [{ points: [{ x: 1, y: 1 }] }] }],
    ['a stroke with no points', { strokes: [{ radius: 3, points: [] }] }],
    ['a radius of zero', { strokes: [{ radius: 0, points: [{ x: 1, y: 1 }] }] }],
  ])('are not read from %s', (_name, geometry) => {
    expect(readBrush(geometry)).toBeNull();
  });
});

describe('paintMask', () => {
  const calls: string[] = [];

  afterEach(() => {
    calls.length = 0;
    vi.restoreAllMocks();
  });

  /** A canvas that writes down what is drawn on it, since jsdom has none to draw on. */
  function stubCanvas(): HTMLCanvasElement {
    const context = new Proxy(
      {},
      {
        get:
          (_target, name: string) =>
          (...args: unknown[]) => {
            calls.push(`${name}(${args.join(',')})`);
          },
        set: (_target, name: string, value: unknown) => {
          calls.push(`${name}=${String(value)}`);
          return true;
        },
      },
    );
    return {
      width: 0,
      height: 0,
      getContext: () => context,
      toBlob: (done: (blob: Blob) => void) => done(new Blob(['png'], { type: 'image/png' })),
    } as unknown as HTMLCanvasElement;
  }

  it('paints a black page the size of the image, and the strokes on it in white', async () => {
    const canvas = stubCanvas();
    vi.spyOn(document, 'createElement').mockReturnValue(canvas);

    const blob = await paintMask(
      {
        strokes: [
          {
            radius: 5,
            points: [
              { x: 10, y: 10 },
              { x: 30, y: 10 },
            ],
          },
        ],
      },
      { width: 200, height: 300 },
    );

    expect([canvas.width, canvas.height]).toEqual([200, 300]);
    expect(calls).toContain('fillStyle=#000000');
    expect(calls).toContain('fillRect(0,0,200,300)');
    expect(calls).toContain('strokeStyle=#ffffff');
    expect(calls).toContain('lineWidth=10');
    expect(calls).toContain('lineTo(30,10)');
    expect(blob.type).toBe('image/png');
  });

  it('paints a disc for a click, which is a stroke of one point', async () => {
    vi.spyOn(document, 'createElement').mockReturnValue(stubCanvas());

    await paintMask(
      { strokes: [{ radius: 7, points: [{ x: 50, y: 60 }] }] },
      { width: 100, height: 100 },
    );

    expect(calls.some((call) => call.startsWith('arc(50,60,7'))).toBe(true);
  });
});
