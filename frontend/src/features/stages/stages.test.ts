import { describe, expect, it } from 'vitest';
import { MESSAGES } from '@/shared/messages';
import { Phase, STAGES, stageNumber } from './stages';

describe('STAGES', () => {
  it('lists the ten stages once each, in the order of the pipeline the API uses', () => {
    expect(STAGES.map((entry) => entry.stage)).toEqual([
      'import',
      'page-split',
      'page-order',
      'geometry',
      'cleanup',
      'layout',
      'background',
      'recognition',
      'proofreading',
      'typesetting',
    ]);
  });

  it('keeps the stages of one phase together, so a phase is one group of the stage bar', () => {
    const phases = STAGES.map((entry) => entry.phase);
    const groups = phases.filter((phase, index) => index === 0 || phases[index - 1] !== phase);
    expect(groups).toEqual(Object.values(Phase));
  });

  it('has a name and a summary for every stage', () => {
    for (const { stage } of STAGES) {
      expect(MESSAGES.stages.names[stage]).not.toBe('');
      expect(MESSAGES.stages.summaries[stage]).not.toBe('');
    }
  });
});

describe('stageNumber', () => {
  it('counts the stages from one', () => {
    expect(stageNumber('import')).toBe(1);
    expect(stageNumber('typesetting')).toBe(10);
  });
});
