import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { BookPlaceBody, BookPlaceSchema, CanvasPositionSchema } from '@/api';
import type { PlaceAddress } from '@/features/place/address';
import { PlaceWriter, Restore, WRITE_DELAY_MS } from '@/features/place/writer';

const ADDRESS: PlaceAddress = { mode: 'workspace', stage: 'geometry', page: 'p-1' };
const CANVAS: CanvasPositionSchema = { zoom: 2.5, centre_x: 0.4, centre_y: 0.6 };
const PLACE: BookPlaceSchema = {
  mode: 'workspace',
  stage: 'geometry',
  page_id: 'p-1',
  scan_id: null,
  source_id: null,
  view: 'page',
  compare: 'off',
  filter: 'all',
  canvas: CANVAS,
  strip_page_id: 'p-7',
  updated_at: '2026-10-02T10:00:00Z',
};

interface Sent {
  body: BookPlaceBody;
  keepalive: boolean;
}

function writerOf(
  address: PlaceAddress | null,
  initial: BookPlaceSchema | null = null,
  failures = 0,
): { writer: PlaceWriter; sent: Sent[]; remembered: BookPlaceSchema[] } {
  const sent: Sent[] = [];
  const remembered: BookPlaceSchema[] = [];
  let toFail = failures;
  const writer = new PlaceWriter({
    address,
    initial,
    send: (body, keepalive) => {
      sent.push({ body, keepalive });
      if (toFail > 0) {
        toFail -= 1;
        return Promise.reject(new Error('offline'));
      }
      return Promise.resolve();
    },
    remember: (place) => remembered.push(place),
  });
  return { writer, sent, remembered };
}

describe('PlaceWriter', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('writes once after a pause in the moves, not after each one', () => {
    const { writer, sent } = writerOf(ADDRESS);
    for (const page of ['p-2', 'p-3', 'p-4']) {
      writer.setAddress({ ...ADDRESS, page });
      vi.advanceTimersByTime(WRITE_DELAY_MS / 2);
    }
    expect(sent).toHaveLength(0);

    vi.advanceTimersByTime(WRITE_DELAY_MS);
    expect(sent.map((entry) => entry.body.page_id)).toEqual(['p-4']);
    expect(sent[0]?.keepalive).toBe(false);
  });

  it('writes nothing for the address the place already names', () => {
    const { writer, sent } = writerOf(ADDRESS, PLACE);
    writer.start();
    writer.setAddress({ ...ADDRESS });
    vi.advanceTimersByTime(WRITE_DELAY_MS * 2);
    writer.stop();
    expect(sent).toHaveLength(0);
  });

  it('writes the address a screen opened at when the place names another', () => {
    const { writer, sent, remembered } = writerOf({ ...ADDRESS, stage: 'cleanup' }, PLACE);
    writer.start();
    expect(remembered.map((place) => place.stage)).toEqual(['cleanup']);
    vi.advanceTimersByTime(WRITE_DELAY_MS);
    writer.stop();
    expect(sent.map((entry) => entry.body.stage)).toEqual(['cleanup']);
  });

  it('writes the address of a book the account has not worked on yet', () => {
    const { writer, sent } = writerOf(ADDRESS);
    writer.start();
    vi.advanceTimersByTime(WRITE_DELAY_MS);
    writer.stop();
    expect(sent.map((entry) => entry.body.page_id)).toEqual(['p-1']);
  });

  it('writes nothing again when the body is the one already written', () => {
    const { writer, sent } = writerOf(ADDRESS, PLACE);
    writer.attachCanvas(() => CANVAS);
    writer.attachStrip(() => 'p-7');
    writer.touch();
    vi.advanceTimersByTime(WRITE_DELAY_MS);
    expect(sent).toHaveLength(0);
  });

  it('asks the canvas and the strip for their position at the moment it writes', () => {
    const { writer, sent } = writerOf(ADDRESS);
    let canvas: CanvasPositionSchema = CANVAS;
    writer.attachCanvas(() => canvas);
    writer.attachStrip(() => 'p-5');
    writer.touch();
    canvas = { zoom: 4, centre_x: 0.1, centre_y: 0.2 };
    vi.advanceTimersByTime(WRITE_DELAY_MS);
    expect(sent[0]?.body).toMatchObject({
      canvas: { zoom: 4, centre_x: 0.1, centre_y: 0.2 },
      strip_page_id: 'p-5',
    });
  });

  it('writes at once, with keepalive, when the page is hidden', () => {
    const { writer, sent } = writerOf(ADDRESS);
    writer.start();
    writer.setAddress({ ...ADDRESS, page: 'p-2' });
    window.dispatchEvent(new Event('pagehide'));
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({ keepalive: true, body: { page_id: 'p-2' } });

    vi.advanceTimersByTime(WRITE_DELAY_MS * 2);
    expect(sent).toHaveLength(1);
    writer.stop();
  });

  it('writes at once when the tab goes to the background', () => {
    const { writer, sent } = writerOf(ADDRESS);
    writer.start();
    writer.setAddress({ ...ADDRESS, page: 'p-2' });
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    document.dispatchEvent(new Event('visibilitychange'));
    expect(sent[0]).toMatchObject({ keepalive: true });
    writer.stop();
    vi.restoreAllMocks();
  });

  it('writes what is pending when the screen goes away, and stops listening', () => {
    const { writer, sent } = writerOf(ADDRESS);
    writer.start();
    writer.setAddress({ ...ADDRESS, page: 'p-2' });
    writer.stop();
    expect(sent).toHaveLength(1);
    window.dispatchEvent(new Event('pagehide'));
    expect(sent).toHaveLength(1);
  });

  it('keeps the position the canvas had when it was taken away', () => {
    const { writer, sent } = writerOf(ADDRESS);
    const detach = writer.attachCanvas(() => CANVAS);
    writer.touch();
    detach();
    vi.advanceTimersByTime(WRITE_DELAY_MS);
    expect(sent[0]?.body.canvas).toEqual(CANVAS);
  });

  it('drops the position of the page the reader has turned away from', () => {
    const { writer, sent } = writerOf(ADDRESS);
    const detach = writer.attachCanvas(() => CANVAS);
    detach();
    writer.setAddress({ ...ADDRESS, page: 'p-2' });
    vi.advanceTimersByTime(WRITE_DELAY_MS);
    expect(sent[0]?.body.canvas).toBeNull();
  });

  it('remembers the place for the screen that opens next, without the position of the old page', () => {
    const { writer, remembered } = writerOf(ADDRESS);
    writer.attachCanvas(() => CANVAS);
    writer.setAddress({ ...ADDRESS, page: 'p-2' });
    expect(remembered).toHaveLength(1);
    expect(remembered[0]).toMatchObject({ page_id: 'p-2', canvas: null });
  });

  it('tries again with the next move when a write fails', async () => {
    const { writer, sent } = writerOf(ADDRESS, null, 1);
    writer.setAddress({ ...ADDRESS, page: 'p-2' });
    await vi.advanceTimersByTimeAsync(WRITE_DELAY_MS);
    expect(sent).toHaveLength(1);

    writer.touch();
    await vi.advanceTimersByTimeAsync(WRITE_DELAY_MS);
    expect(sent).toHaveLength(2);
  });

  it('writes nothing while the screen has no address', () => {
    const { writer, sent } = writerOf(null);
    writer.touch();
    vi.advanceTimersByTime(WRITE_DELAY_MS);
    expect(sent).toHaveLength(0);
  });
});

describe('PlaceWriter restore', () => {
  it('hands the canvas and the strip back to the screen at the address of the place, once', () => {
    const { writer } = writerOf(ADDRESS, PLACE);
    expect(writer.takeRestore(Restore.Canvas)).toEqual(CANVAS);
    expect(writer.takeRestore(Restore.Canvas)).toBeNull();
    expect(writer.takeRestore(Restore.Strip)).toBe('p-7');
    expect(writer.takeRestore(Restore.Strip)).toBeNull();
  });

  it('hands nothing to a screen at another address', () => {
    const { writer } = writerOf({ ...ADDRESS, page: 'p-2' }, PLACE);
    expect(writer.takeRestore(Restore.Canvas)).toBeNull();
    expect(writer.takeRestore(Restore.Strip)).toBeNull();
  });

  it('hands nothing back when the account has no place yet', () => {
    const { writer } = writerOf(ADDRESS);
    expect(writer.takeRestore(Restore.Canvas)).toBeNull();
  });

  it('hands nothing back after the reader moved to another page', () => {
    const { writer } = writerOf(ADDRESS, PLACE);
    writer.setAddress({ ...ADDRESS, page: 'p-2' });
    expect(writer.takeRestore(Restore.Canvas)).toBeNull();
  });
});
