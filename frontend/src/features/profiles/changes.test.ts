import { describe, expect, it } from 'vitest';
import { deskew, deskewMethods, processor, step } from '@/features/processing/fixtures';
import {
  addStep,
  draftOf,
  moveStep,
  removeStep,
  setStepParams,
  toggleStep,
} from '@/features/processing/recipe';
import { profileChanges } from '@/features/profiles/changes';

/**
 * How the steps on the screen differ from the steps of the profile: steps are matched by the identifier the server gave
 * them, a step the draft added counts as added, and a change of the order is reported once.
 */

const CATALOGUE = [deskew(), processor('geometry.crop', { title: 'Select content' })];
const PROFILE = [
  step('geometry.deskew', { params: { max_angle: 5, min_confidence: 0.3 } }),
  step('geometry.crop'),
  step('geometry.perspective'),
];

describe('profileChanges', () => {
  it('finds nothing when the steps are those of the profile', () => {
    expect(profileChanges(PROFILE, draftOf({ steps: PROFILE }), CATALOGUE)).toEqual([]);
  });

  it('names a step that was switched off, and one switched on again', () => {
    const off = toggleStep(draftOf({ steps: PROFILE }), 'step-1');

    expect(profileChanges(PROFILE, off, CATALOGUE)).toEqual([
      { kind: 'switched', title: 'Select content', enabled: false },
    ]);
    expect(
      profileChanges(
        [step('geometry.crop', { enabled: false })],
        draftOf({ steps: [step('geometry.crop')] }),
        CATALOGUE,
      ),
    ).toEqual([{ kind: 'switched', title: 'Select content', enabled: true }]);
  });

  it('names the fields whose values changed by their titles, not by their names in the schema', () => {
    const edited = setStepParams(draftOf({ steps: PROFILE }), 'step-0', {
      max_angle: 9,
      min_confidence: 0.5,
    });

    expect(profileChanges(PROFILE, edited, CATALOGUE)).toEqual([
      { kind: 'params', title: 'Deskew', fields: ['Largest slant', 'Least confidence'] },
    ]);
  });

  it('finds the title of a field in the schema of one of the methods of a processor', () => {
    const saved = [step('geometry.deskew', { params: { method: 'projection', max_angle: 5 } })];
    const edited = setStepParams(draftOf({ steps: saved }), 'step-0', {
      method: 'baselines',
      max_angle: 5,
    });

    expect(profileChanges(saved, edited, [deskewMethods()])).toEqual([
      { kind: 'params', title: 'Deskew', fields: ['Method'] },
    ]);
  });

  it('falls back on the key of a processor the catalogue lacks', () => {
    const off = toggleStep(draftOf({ steps: PROFILE }), 'step-2');

    expect(profileChanges(PROFILE, off, CATALOGUE)).toEqual([
      { kind: 'switched', title: 'geometry.perspective', enabled: false },
    ]);
  });

  it('counts a step the draft added as added, and a step it took out as removed, after the others', () => {
    const added = addStep(removeStep(draftOf({ steps: PROFILE }), 'step-1'), deskew());

    expect(profileChanges(PROFILE, added, CATALOGUE)).toEqual([
      { kind: 'added', title: 'Deskew' },
      { kind: 'removed', title: 'Select content' },
    ]);
  });

  it('reports a change of the order once, however far the step was moved', () => {
    const moved = moveStep(draftOf({ steps: PROFILE }), 'step-2', 'step-0');

    expect(profileChanges(PROFILE, moved, CATALOGUE)).toEqual([{ kind: 'order' }]);
  });

  it('does not count a step that was added or taken out as a change of the order', () => {
    const shorter = removeStep(draftOf({ steps: PROFILE }), 'step-0');

    expect(profileChanges(PROFILE, shorter, CATALOGUE)).toEqual([
      { kind: 'removed', title: 'Deskew' },
    ]);
  });
});
