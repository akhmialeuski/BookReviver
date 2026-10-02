import { describe, expect, it } from 'vitest';
import { isPlacement, PLACEMENT_KEY, pictureFor } from '@/features/editors/placement';
import { Picture } from '@/features/editors/types';

/** The step that places the block of text on a page, whose frame lies on the page it made and not on what it read. */

describe('placement', () => {
  it('knows the step that places the block of text on the page', () => {
    expect(isPlacement(PLACEMENT_KEY)).toBe(true);
    expect(isPlacement('geometry.crop')).toBe(false);
  });

  it('lays the frame of the placing step on the page it made, and the frame of the crop on what it read', () => {
    expect(pictureFor(Picture.Input, PLACEMENT_KEY)).toBe(Picture.Output);
    expect(pictureFor(Picture.Input, 'geometry.crop')).toBe(Picture.Input);
  });

  it('leaves the picture of an editor that does not lie on the input as it is', () => {
    expect(pictureFor(Picture.Scan, PLACEMENT_KEY)).toBe(Picture.Scan);
  });
});
