import { describe, expect, it } from 'vitest';
import { stepChain, stepVersion } from '@/features/editors/chain';
import { step, version } from '@/features/processing/fixtures';

/** Finding the versions that made the current one of a stage, one for each step. */

const PERSPECTIVE = version('v1', {
  processor: { key: 'geometry.perspective', version: '1' },
  input_id: 'earlier-stage',
});
const DESKEW = version('v2', { input_id: 'v1' });
const CROP = version('v3', {
  processor: { key: 'geometry.crop', version: '1' },
  input_id: 'v2',
});

describe('stepChain', () => {
  it('follows the versions back from the current one to the first step, which is first in the list', () => {
    expect(stepChain([CROP, PERSPECTIVE, DESKEW], CROP).map((entry) => entry.id)).toEqual([
      'v1',
      'v2',
      'v3',
    ]);
  });

  it('stops at the version of an earlier stage, which is not among the versions of this one', () => {
    expect(stepChain([PERSPECTIVE], PERSPECTIVE)).toEqual([PERSPECTIVE]);
  });

  it('stops at a version that is missing, and at a version of another stage', () => {
    expect(stepChain([CROP], CROP)).toEqual([CROP]);
    const other = version('v2', { input_id: 'v1', stage: 'cleanup' });
    expect(stepChain([PERSPECTIVE, other, CROP], CROP).map((entry) => entry.id)).toEqual(['v3']);
  });

  it.each([null, undefined])('gives none when there is no current version (%s)', (head) => {
    expect(stepChain([CROP], head)).toEqual([]);
  });

  it('leaves out the versions of another run that share an input with the chain', () => {
    const redone = version('redone', { input_id: 'v1' });

    expect(stepChain([PERSPECTIVE, DESKEW, CROP, redone], redone).map((entry) => entry.id)).toEqual(
      ['v1', 'redone'],
    );
  });

  it('stops on a version that reads itself', () => {
    const loop = version('loop', { input_id: 'loop' });

    expect(stepChain([loop], loop)).toEqual([loop]);
  });
});

describe('stepVersion', () => {
  const chain = [PERSPECTIVE, DESKEW, CROP];
  const steps = [step('geometry.perspective'), step('geometry.deskew'), step('geometry.crop')];

  it('gives the version a step made', () => {
    expect(stepVersion(chain, steps, 2)).toBe(CROP);
    expect(stepVersion(chain, steps, 1)).toBe(DESKEW);
    expect(stepVersion(chain, steps, 0)).toBe(PERSPECTIVE);
  });

  it('gives nothing for a step the page has not reached, or one whose processor changed', () => {
    expect(stepVersion([PERSPECTIVE], steps, 2)).toBeNull();
    const changed = [step('geometry.perspective'), step('geometry.dewarp'), step('geometry.crop')];
    expect(stepVersion(chain, changed, 1)).toBeNull();
  });

  it('tells two steps of one processor apart by their place, and counts only the steps that are on', () => {
    const second = version('v4', { input_id: 'v2' });
    const twice = [
      step('geometry.deskew', { step_id: 'first' }),
      step('geometry.crop', { enabled: false }),
      step('geometry.deskew', { step_id: 'second' }),
    ];

    expect(stepVersion([DESKEW, second], twice, 2)).toBe(second);
    expect(stepVersion([DESKEW, second], twice, 0)).toBe(DESKEW);
    expect(stepVersion([DESKEW, second], twice, 1)).toBeNull();
  });
});
