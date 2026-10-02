import { describe, expect, it } from 'vitest';
import { FIRST_STAGE, parseStage, startStage } from '@/features/stages/parse';
import { STAGES } from '@/features/stages/stages';

describe('parseStage', () => {
  it('reads every stage of the pipeline', () => {
    for (const { stage } of STAGES) {
      expect(parseStage(stage)).toBe(stage);
    }
  });

  it('refuses text that names no stage', () => {
    for (const value of ['', 'Import', 'ocr', 'import ', 'constructor', '__proto__']) {
      expect(parseStage(value)).toBeNull();
    }
  });

  it('refuses a value that is not text', () => {
    for (const value of [undefined, null, 4, {}, ['import']]) {
      expect(parseStage(value)).toBeNull();
    }
  });
});

describe('startStage', () => {
  it('prefers the stage the reader left the book on', () => {
    expect(startStage('geometry', 'page-order')).toBe('geometry');
  });

  it('falls back to the next stage of the book', () => {
    expect(startStage(null, 'page-order')).toBe('page-order');
  });

  it('opens a new book on the first stage', () => {
    expect(startStage(null, null)).toBe(FIRST_STAGE);
    expect(FIRST_STAGE).toBe('import');
  });
});
