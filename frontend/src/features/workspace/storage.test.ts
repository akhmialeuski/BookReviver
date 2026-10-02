import { afterEach, describe, expect, it, vi } from 'vitest';
import { browserStorage } from '@/features/workspace/storage';

const BLOCKED_STORAGE = {
  getItem: () => {
    throw new DOMException('blocked', 'SecurityError');
  },
  setItem: () => {
    throw new DOMException('full', 'QuotaExceededError');
  },
};

describe('the browser storage', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('gives back what was written', () => {
    browserStorage.setItem('bookreviver.workspace', '{"strip":18}');
    expect(browserStorage.getItem('bookreviver.workspace')).toBe('{"strip":18}');
  });

  it('never throws and remembers nothing when the browser forbids it', () => {
    vi.stubGlobal('localStorage', BLOCKED_STORAGE);
    expect(() => browserStorage.setItem('any', 'value')).not.toThrow();
    expect(browserStorage.getItem('any')).toBeNull();
  });
});
