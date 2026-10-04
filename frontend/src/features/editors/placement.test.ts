import { describe, expect, it } from 'vitest';
import { isPlacement, PLACEMENT_KEY } from '@/features/editors/placement';

describe('placement', () => {
  it('knows the step that places the content box on the page', () => {
    expect(isPlacement(PLACEMENT_KEY)).toBe(true);
    expect(isPlacement('geometry.crop')).toBe(false);
  });
});
