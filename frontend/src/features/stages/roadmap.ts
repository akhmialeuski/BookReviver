import { CropIcon, Grid3x3Icon, type LucideIcon, ScanIcon } from 'lucide-react';
import type { Stage } from '@/api';

/**
 * The steps a stage will get, which its panel lists under the steps it has with the word "Soon".
 *
 * A step is listed here by the key its processor will have, so the moment the catalogue has that processor the step is
 * a real one and leaves the list, with no change to the interface. The words are in `MESSAGES.processing.soon.steps`.
 */

/** The keys the planned processors will have in the catalogue. */
export type RoadmapKey = 'geometry.perspective' | 'geometry.dewarp' | 'geometry.crop';

/** A step that is planned and not built. */
export interface RoadmapStep {
  key: RoadmapKey;
  icon: LucideIcon;
}

/** The planned steps of each stage that has some listed. */
const ROADMAP: Partial<Record<Stage, readonly RoadmapStep[]>> = {
  geometry: [
    { key: 'geometry.perspective', icon: CropIcon },
    { key: 'geometry.dewarp', icon: Grid3x3Icon },
    { key: 'geometry.crop', icon: ScanIcon },
  ],
};

/**
 * Give the planned steps of a stage that the catalogue does not have yet.
 *
 * @param stage The stage.
 * @param installed The keys of the processors in the catalogue.
 */
export function roadmapOf(stage: Stage, installed: ReadonlySet<string>): RoadmapStep[] {
  return (ROADMAP[stage] ?? []).filter((step) => !installed.has(step.key));
}
