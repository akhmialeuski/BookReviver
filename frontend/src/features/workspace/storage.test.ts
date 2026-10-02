import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  browserStorage,
  recallStage,
  rememberStage,
  type WorkspaceStorage,
} from '@/features/workspace/storage';

function memoryStorage(): WorkspaceStorage {
  const values = new Map<string, string>();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => void values.set(key, value),
  };
}

const BLOCKED_STORAGE = {
  getItem: () => {
    throw new DOMException('blocked', 'SecurityError');
  },
  setItem: () => {
    throw new DOMException('full', 'QuotaExceededError');
  },
};

describe('the last stage of a book', () => {
  it('is given back for the book it was remembered for', () => {
    const storage = memoryStorage();
    rememberStage('book-1', 'geometry', storage);
    expect(recallStage('book-1', storage)).toBe('geometry');
  });

  it('is kept apart for every book', () => {
    const storage = memoryStorage();
    rememberStage('book-1', 'geometry', storage);
    rememberStage('book-2', 'page-order', storage);
    expect(recallStage('book-1', storage)).toBe('geometry');
    expect(recallStage('book-2', storage)).toBe('page-order');
    expect(recallStage('book-3', storage)).toBeNull();
  });

  it('is nothing when the record is not a stage', () => {
    const storage = memoryStorage();
    storage.setItem('bookreviver.lastStage.book-1', 'ocr');
    expect(recallStage('book-1', storage)).toBeNull();
  });

  it('is read and written through the browser storage by default', () => {
    rememberStage('book-9', 'cleanup');
    expect(recallStage('book-9')).toBe('cleanup');
  });
});

describe('the browser storage when the browser forbids it', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('never throws and remembers nothing', () => {
    vi.stubGlobal('localStorage', BLOCKED_STORAGE);
    expect(() => rememberStage('book-1', 'geometry')).not.toThrow();
    expect(recallStage('book-1')).toBeNull();
    expect(browserStorage.getItem('any')).toBeNull();
  });
});
