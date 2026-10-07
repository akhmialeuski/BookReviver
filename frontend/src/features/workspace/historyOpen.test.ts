import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  HISTORY_OPEN_KEY,
  readHistoryOpen,
  writeHistoryOpen,
} from '@/features/workspace/historyOpen';
import type { WorkspaceStorage } from '@/features/workspace/storage';

/** The choice of whether the history of a page is open: kept once for the viewer, and collapsed when storage fails. */

function memoryStorage(): WorkspaceStorage & { values: Map<string, string> } {
  const values = new Map<string, string>();
  return {
    values,
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => {
      values.set(key, value);
    },
  };
}

describe('the remembered state of the history', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('is collapsed until the reader opens it', () => {
    expect(readHistoryOpen(memoryStorage())).toBe(false);
  });

  it('reads back what was kept, under one key for every step', () => {
    const storage = memoryStorage();

    writeHistoryOpen(true, storage);
    expect(readHistoryOpen(storage)).toBe(true);
    expect([...storage.values.keys()]).toEqual([HISTORY_OPEN_KEY]);

    writeHistoryOpen(false, storage);
    expect(readHistoryOpen(storage)).toBe(false);
  });

  it('reads as collapsed and loses the choice when the browser blocks its storage', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });

    expect(readHistoryOpen()).toBe(false);
    expect(() => writeHistoryOpen(true)).not.toThrow();
  });
});
