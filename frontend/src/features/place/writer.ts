import type { BookPlaceBody, BookPlaceSchema, CanvasPositionSchema } from '@/api';
import {
  addressOfPlace,
  bodyOf,
  type PlaceAddress,
  placeOfBody,
  sameAddress,
} from '@/features/place/address';

/**
 * Writes the place of a book to the server while a reader moves about it.
 *
 * A screen tells the writer where the reader is, which is its address, and the canvas and the strip tell it when they
 * move. The writer waits for a pause in the moves before it writes, so paging through a book is not a stream of
 * requests, and it writes at once when the reader leaves the screen or the page is hidden, with the request marked
 * `keepalive` so closing the tab does not cancel it. The zoom and the first page of the strip are not tracked as they
 * change: the writer asks the canvas and the strip for them at the moment it writes.
 *
 * A writer also hands back, once, the position of the canvas and of the strip that the place held when the screen
 * opened, but only to a screen at the very address the place named, so a link to another page of the book does not
 * start from the zoom of an unrelated one.
 */

/** How long the writer waits after the last move before it writes, in milliseconds. */
export const WRITE_DELAY_MS = 1000;

/** What can be handed back once the screen has opened: the position of the canvas, or the first page of the strip. */
export const Restore = {
  Canvas: 'canvas',
  Strip: 'strip',
} as const;

/** One kind of restore (derived from {@link Restore}). */
export type Restore = (typeof Restore)[keyof typeof Restore];

/** Gives the position of the canvas now, or null while the canvas shows nothing yet. */
export type CanvasSource = () => CanvasPositionSchema | null;

/** Gives the identifier of the first page in sight in the strip or the grid, or null when there is none. */
export type StripSource = () => string | null;

/** What a writer needs from its surroundings. */
export interface PlaceWriterOptions {
  /** Where the reader is when the screen opens, or null while the screen does not know yet. */
  address: PlaceAddress | null;
  /** The place the server holds, or null when the account has not worked on the book. */
  initial: BookPlaceSchema | null;
  /** Writes a body to the server; the writer ignores the answer and tries again with the next move. */
  send: (body: BookPlaceBody, keepalive: boolean) => Promise<void>;
  /** Makes the screens that open next read the place that is being written, before the server has answered. */
  remember: (place: BookPlaceSchema) => void;
  /** Pause after the last move, for tests that cannot wait a second. */
  delayMs?: number;
}

/** A value read from a source, kept for the address it was read at. */
interface Reading<ValueT> {
  value: ValueT;
  address: PlaceAddress;
}

export class PlaceWriter {
  private address: PlaceAddress | null;
  private readonly send: PlaceWriterOptions['send'];
  private readonly remember: PlaceWriterOptions['remember'];
  private readonly delayMs: number;
  private readonly restoreAddress: PlaceAddress | null;
  private restoreCanvas: CanvasPositionSchema | null;
  private restoreStrip: string | null;
  private canvasSource: CanvasSource | null = null;
  private stripSource: StripSource | null = null;
  private lastCanvas: Reading<CanvasPositionSchema> | null = null;
  private lastStrip: Reading<string> | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private sent: string | null;

  constructor(options: PlaceWriterOptions) {
    this.address = options.address;
    this.send = options.send;
    this.remember = options.remember;
    this.delayMs = options.delayMs ?? WRITE_DELAY_MS;
    const { initial } = options;
    this.restoreAddress = initial === null ? null : addressOfPlace(initial);
    this.restoreCanvas = initial?.canvas ?? null;
    this.restoreStrip = initial?.strip_page_id ?? null;
    this.sent =
      initial === null || this.restoreAddress === null
        ? null
        : JSON.stringify(bodyOf(this.restoreAddress, initial.canvas, initial.strip_page_id));
  }

  /**
   * Start listening for the page being hidden, and note a screen that opened somewhere other than the place names.
   *
   * The writer may be started again after it was stopped.
   */
  start(): void {
    window.addEventListener('pagehide', this.onHide);
    document.addEventListener('visibilitychange', this.onVisibility);
    const { address, restoreAddress } = this;
    if (address !== null && (restoreAddress === null || !sameAddress(address, restoreAddress))) {
      this.remember(placeOfBody(bodyOf(address, null, null), new Date()));
      this.touch();
    }
  }

  /** Stop listening, and write what the last moves left unwritten. */
  stop(): void {
    window.removeEventListener('pagehide', this.onHide);
    document.removeEventListener('visibilitychange', this.onVisibility);
    this.flush(false);
  }

  /** Say where the reader is now; the writer writes after a pause if that is not where the server has them. */
  setAddress(address: PlaceAddress | null): void {
    if (address === null) {
      this.address = null;
      return;
    }
    if (this.address !== null && sameAddress(this.address, address)) {
      return;
    }
    this.address = address;
    // The canvas and the strip still show the page the reader just left, so the place that is remembered has neither
    this.remember(placeOfBody(bodyOf(address, null, null), new Date()));
    this.touch();
  }

  /** Note that the canvas or the strip moved, and write after a pause. */
  touch(): void {
    if (this.timer !== null) {
      clearTimeout(this.timer);
    }
    this.timer = setTimeout(() => {
      this.timer = null;
      this.write(false);
    }, this.delayMs);
  }

  /**
   * Let the canvas be asked for its position when the writer writes.
   *
   * @returns A function that takes the canvas away again, after remembering the position it had then.
   */
  attachCanvas(source: CanvasSource): () => void {
    this.canvasSource = source;
    return () => {
      this.lastCanvas = this.reading(source) ?? this.lastCanvas;
      if (this.canvasSource === source) {
        this.canvasSource = null;
      }
    };
  }

  /**
   * Let the strip or the grid be asked for its first page when the writer writes.
   *
   * @returns A function that takes the strip away again, after remembering the page it had then.
   */
  attachStrip(source: StripSource): () => void {
    this.stripSource = source;
    return () => {
      this.lastStrip = this.reading(source) ?? this.lastStrip;
      if (this.stripSource === source) {
        this.stripSource = null;
      }
    };
  }

  /**
   * Hand back the position the place held when the screen opened, once.
   *
   * @param kind Which position: the canvas, or the first page of the strip.
   * @returns The position, or null when the place had none, was handed over already, or names another address than
   * the screen has now.
   */
  takeRestore(kind: typeof Restore.Canvas): CanvasPositionSchema | null;
  takeRestore(kind: typeof Restore.Strip): string | null;
  takeRestore(kind: Restore): CanvasPositionSchema | string | null {
    const here = this.address;
    if (here === null || this.restoreAddress === null || !sameAddress(here, this.restoreAddress)) {
      return null;
    }
    if (kind === Restore.Canvas) {
      const taken = this.restoreCanvas;
      this.restoreCanvas = null;
      return taken;
    }
    const taken = this.restoreStrip;
    this.restoreStrip = null;
    return taken;
  }

  /** Write at once what a pause has not written yet. */
  flush(keepalive: boolean): void {
    if (this.timer === null) {
      return;
    }
    clearTimeout(this.timer);
    this.timer = null;
    this.write(keepalive);
  }

  private readonly onHide = (): void => this.flush(true);

  private readonly onVisibility = (): void => {
    if (document.visibilityState === 'hidden') {
      this.flush(true);
    }
  };

  /** Ask a source for its value now, and keep it for the address the reader is at. */
  private reading<ValueT>(source: (() => ValueT | null) | null): Reading<ValueT> | null {
    const value = source?.() ?? null;
    return value === null || this.address === null ? null : { value, address: this.address };
  }

  /** The value a source gives now, or the one it gave last at this address when it has nothing to say. */
  private latest<ValueT>(
    source: (() => ValueT | null) | null,
    last: Reading<ValueT> | null,
  ): Reading<ValueT> | null {
    const fresh = this.reading(source);
    if (fresh !== null) {
      return fresh;
    }
    return last !== null && this.address !== null && sameAddress(last.address, this.address)
      ? last
      : null;
  }

  private write(keepalive: boolean): void {
    if (this.address === null) {
      return;
    }
    this.lastCanvas = this.latest(this.canvasSource, this.lastCanvas);
    this.lastStrip = this.latest(this.stripSource, this.lastStrip);
    const body = bodyOf(
      this.address,
      this.lastCanvas?.value ?? null,
      this.lastStrip?.value ?? null,
    );
    const key = JSON.stringify(body);
    if (key === this.sent) {
      return;
    }
    const before = this.sent;
    this.sent = key;
    this.remember(placeOfBody(body, new Date()));
    void this.send(body, keepalive).catch(() => {
      // A place is a convenience, so a failed write is tried again with the next move and never shown
      if (this.sent === key) {
        this.sent = before;
      }
    });
  }
}
