import { crc32, deflateSync } from 'node:zlib';

/**
 * A minimal PNG encoder for the pages the scenarios upload: a solid colour, so no image library is needed and
 * every page can have a colour of its own, which keeps the server from rejecting one as a duplicate of another.
 */

const SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const BIT_DEPTH = 8;
const COLOR_TYPE_RGB = 2;
const FILTER_NONE = 0;

function chunk(type: string, data: Buffer): Buffer {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const checksum = Buffer.alloc(4);
  checksum.writeUInt32BE(crc32(body));
  return Buffer.concat([length, body, checksum]);
}

/** Encode rows of pixels, each starting with its filter byte, as a PNG of colour. */
function encode(width: number, height: number, rows: Buffer): Buffer {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = BIT_DEPTH;
  header[9] = COLOR_TYPE_RGB;
  return Buffer.concat([
    SIGNATURE,
    chunk('IHDR', header),
    chunk('IDAT', deflateSync(rows)),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

/** Encode a PNG of one colour, `[red, green, blue]` each 0 to 255. */
export function solidPng(
  width: number,
  height: number,
  color: readonly [number, number, number],
): Buffer {
  const row = Buffer.concat([
    Buffer.from([FILTER_NONE]),
    Buffer.from(Array.from({ length: width }, () => color).flat()),
  ]);
  return encode(width, height, Buffer.concat(Array.from({ length: height }, () => row)));
}

/** A colour, `[red, green, blue]` each 0 to 255. */
type Color = readonly [number, number, number];

/** A point of a page, in pixels from its top left corner. */
type Corner = readonly [number, number];

const BINDING: Color = [46, 42, 40];
const PAPER: Color = [228, 208, 164];
const INK: Color = [35, 30, 28];
const LINE_PITCH_PX = 22;
const LINE_HEIGHT_PX = 9;
const WORD_GAP_PX = 10;
const SHEET_MARGIN_SHARE = 0.12;
// A speck of dust is a square of this side, and the specks stand in the gaps of lines this many lines apart
const DUST_SIDE_PX = 2;
const DUST_LINE_STEP = 3;
// How much of the square of the share of the width is added to its cube in the shift of a bent column
const BEND_QUADRATIC_WEIGHT = 0.3;

/** How a sheet differs from a flat scan of text of the usual size. */
export interface SheetShape {
  /**
   * How large the text is over its usual size, which a scan taken closer to the page or at a higher resolution shows:
   * the lines stand farther apart and the words are larger, and the scan is made larger by the same factor by the caller
   * so that the sheet stays in proportion.
   */
  textScale?: number;
  /**
   * How far the right edge of the scene is moved down, which bends the lines and the top and the bottom edge of the
   * sheet as a page bends into a gutter, or 0 for a flat page.
   */
  bendPx?: number;
  /** How many specks of dust lie on the paper, in the gaps between the lines. */
  dust?: number;
}

/** Tell whether a point lies inside a convex quadrilateral whose corners go round in order. */
function inside(corners: readonly Corner[], x: number, y: number): boolean {
  let sign = 0;
  for (const [index, [x1, y1]] of corners.entries()) {
    const [x2, y2] = corners[(index + 1) % corners.length] ?? [x1, y1];
    const side = Math.sign((x2 - x1) * (y - y1) - (y2 - y1) * (x - x1));
    if (side !== 0 && sign !== 0 && side !== sign) {
      return false;
    }
    sign = side === 0 ? sign : side;
  }
  return true;
}

/**
 * Encode a PNG of a sheet of paper laid on a dark binding, with lines of words on it.
 *
 * The sheet is a quadrilateral a little narrower at the top and turned by a small angle, so it has to be straightened,
 * and the words stand in rows inside its margins, so the frame of the content is smaller than the sheet. The seed makes
 * the words of one page differ from those of another, so the server does not take a page for a duplicate.
 *
 * @param width Width of the scan in pixels.
 * @param height Height of the scan in pixels.
 * @param seed A number that decides the widths of the words.
 * @param shape How the text is sized and the sheet is bent, each left at its usual value when absent.
 */
export function sheetPng(
  width: number,
  height: number,
  seed: number,
  { textScale = 1, bendPx = 0, dust = 0 }: SheetShape = {},
): Buffer {
  const pitch = LINE_PITCH_PX * textScale;
  const lineHeight = LINE_HEIGHT_PX * textScale;
  const gap = WORD_GAP_PX * textScale;
  const turn = 0.03;
  const inset = 0.04;
  const centre = [width / 2, height / 2] as const;
  const flat: readonly Corner[] = [
    [width * 0.14, height * 0.08],
    [width * 0.86, height * 0.08],
    [width * 0.86, height * 0.92],
    [width * 0.14, height * 0.92],
  ];
  const corners = flat.map(([x, y], index): Corner => {
    const dx = (x - centre[0]) * (index < 2 ? 1 - inset : 1);
    const dy = y - centre[1];
    return [
      centre[0] + dx * Math.cos(turn) + dy * Math.sin(turn),
      centre[1] - dx * Math.sin(turn) + dy * Math.cos(turn),
    ];
  });
  const left = width * (0.14 + SHEET_MARGIN_SHARE * 0.72);
  const right = width * (0.86 - SHEET_MARGIN_SHARE * 0.72);
  const top = height * (0.08 + SHEET_MARGIN_SHARE);
  const bottom = height * (0.92 - SHEET_MARGIN_SHARE);

  // The words of a line: runs of ink with gaps, from the seed
  const words = new Map<number, [number, number][]>();
  let state = seed;
  const next = (): number => {
    state = (state * 1103515245 + 12345) % 2147483648;
    return state / 2147483648;
  };
  for (let lineTop = top; lineTop < bottom; lineTop += pitch) {
    const runs: [number, number][] = [];
    for (let at = left; at < right; ) {
      const end = Math.min(at + (18 + next() * 50) * textScale, right);
      runs.push([at, end]);
      at = end + gap + next() * 8 * textScale;
    }
    words.set(Math.round(lineTop), runs);
  }

  // A bend moves each column of the scene down by a shift that grows to the right edge, as a page bends into a gutter
  const shifts = Array.from({ length: width }, (_, x) => {
    const share = x / width;
    return bendPx * (share ** 3 + BEND_QUADRATIC_WEIGHT * share ** 2);
  });
  const lines = [...words.entries()];

  // Specks of dust in the middle of the gap between two lines, far from every letter and inside the block of text, so that
  // the steps that cut the page to its text keep them
  const gapMiddle = (lineHeight + (pitch - lineHeight) / 2 - DUST_SIDE_PX / 2) | 0;
  const lineTops = [...words.keys()];
  const specks = Array.from({ length: dust }, (_, index): Corner => {
    const lineTop =
      lineTops[(index * DUST_LINE_STEP + 1) % Math.max(lineTops.length - 1, 1)] ?? top;
    return [Math.round(left + (0.2 + 0.6 * next()) * (right - left)), lineTop + gapMiddle];
  });

  const rows: Buffer[] = [];
  for (let y = 0; y < height; y += 1) {
    const row = Buffer.alloc(1 + width * 3);
    row[0] = FILTER_NONE;
    for (let x = 0; x < width; x += 1) {
      // The row of the flat scene that this pixel shows
      const flatY = y - (shifts[x] ?? 0);
      let color: Color = BINDING;
      if (inside(corners, x, flatY)) {
        const line = lines.find(([lineTop]) => flatY >= lineTop && flatY < lineTop + lineHeight);
        const onWord = line?.[1].some(([from, to]) => x >= from && x < to) ?? false;
        const onDust = specks.some(
          ([at, from]) =>
            x >= at && x < at + DUST_SIDE_PX && flatY >= from && flatY < from + DUST_SIDE_PX,
        );
        color = onWord || onDust ? INK : PAPER;
      }
      row.set(color, 1 + x * 3);
    }
    rows.push(row);
  }
  return encode(width, height, Buffer.concat(rows));
}
