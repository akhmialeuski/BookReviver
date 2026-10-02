import { describe, expect, it } from 'vitest';
import { roadmapOf } from '@/features/stages/roadmap';

describe('roadmapOf', () => {
  it('lists the planned steps of Geometry while none is installed', () => {
    expect(roadmapOf('geometry', new Set(['geometry.deskew'])).map((step) => step.key)).toEqual([
      'geometry.perspective',
      'geometry.dewarp',
      'geometry.crop',
    ]);
  });

  it('drops a step once its processor is in the catalogue', () => {
    const keys = roadmapOf('geometry', new Set(['geometry.dewarp'])).map((step) => step.key);

    expect(keys).toEqual(['geometry.perspective', 'geometry.crop']);
  });

  it('lists nothing for a stage with no plan', () => {
    expect(roadmapOf('page-split', new Set())).toEqual([]);
  });
});
