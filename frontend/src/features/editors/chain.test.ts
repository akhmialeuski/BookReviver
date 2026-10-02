import { describe, expect, it } from 'vitest';
import { stepChain, stepVersions } from '@/features/editors/chain';
import { version } from '@/features/processing/fixtures';

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
});

describe('stepVersions', () => {
  const chain = [PERSPECTIVE, DESKEW, CROP];

  it('gives the version of a step and the version it read', () => {
    expect(stepVersions(chain, 'geometry.crop')).toEqual({ made: CROP, read: DESKEW });
    expect(stepVersions(chain, 'geometry.deskew')).toEqual({ made: DESKEW, read: PERSPECTIVE });
  });

  it('gives no version read for the first step, which reads the picture before the stage', () => {
    expect(stepVersions(chain, 'geometry.perspective')).toEqual({ made: PERSPECTIVE, read: null });
  });

  it('gives nothing for a step the page has no version of', () => {
    expect(stepVersions(chain, 'geometry.unknown')).toEqual({ made: null, read: null });
  });
});
