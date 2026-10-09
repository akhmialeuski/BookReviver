import { describe, expect, it } from 'vitest';
import type { ProfileFileSchema } from '@/api';
import {
  FileProblem,
  fileNameOf,
  readProfileFile,
  writeProfileFile,
} from '@/features/profiles/profileFile';

/**
 * The file a profile is exchanged by: what is written for the download, and which chosen files are turned away before a
 * request is spent on them.
 */

const FILE: ProfileFileSchema = {
  version: 2,
  stage: 'geometry',
  name: 'Photographed book',
  order: 'usual',
  steps: [
    {
      processor_key: 'geometry.deskew',
      params: { max_angle: 9 },
      enabled: true,
    },
    { processor_key: 'geometry.crop', params: {}, enabled: false },
  ],
};

describe('writeProfileFile and readProfileFile', () => {
  it('reads back what was written, with its steps in their order', () => {
    const read = readProfileFile(writeProfileFile(FILE));

    expect(read).toEqual({ ok: true, file: FILE });
  });

  it('writes indented text that ends in a newline, so a person can read and diff it', () => {
    const text = writeProfileFile(FILE);

    expect(text.endsWith('}\n')).toBe(true);
    expect(text).toContain('\n  "version": 2,');
  });
});

describe('readProfileFile', () => {
  it('turns away text that is not JSON', () => {
    expect(readProfileFile('{"version": 1,')).toEqual({ ok: false, problem: FileProblem.NotJson });
  });

  it.each([
    ['an array', '[]'],
    ['a number', '3'],
    ['no steps', JSON.stringify({ ...FILE, steps: undefined })],
    ['no name', JSON.stringify({ ...FILE, name: undefined })],
    ['no stage', JSON.stringify({ ...FILE, stage: undefined })],
    ['no version', JSON.stringify({ ...FILE, version: undefined })],
    ['steps that are not a list', JSON.stringify({ ...FILE, steps: 'deskew' })],
  ])('turns away JSON that is not a profile: %s', (_name, text) => {
    expect(readProfileFile(text)).toEqual({ ok: false, problem: FileProblem.NotProfile });
  });

  it('passes on a file of the first version, whose steps may name the pages they process, for the server to read', () => {
    const first = {
      ...FILE,
      version: 1,
      steps: [{ processor_key: 'geometry.deskew', params: {}, enabled: true, applies_to: 'all' }],
    };

    expect(readProfileFile(JSON.stringify(first))).toEqual({ ok: true, file: first });
  });

  it('turns away a version of the format it does not know', () => {
    expect(readProfileFile(JSON.stringify({ ...FILE, version: 9 }))).toEqual({
      ok: false,
      problem: FileProblem.Version,
    });
  });

  it('leaves the judgement of a step to the server', () => {
    const odd = { ...FILE, steps: [{ processor_key: 'geometry.gone', params: { x: 1 } }] };

    expect(readProfileFile(JSON.stringify(odd))).toEqual({ ok: true, file: odd });
  });
});

describe('fileNameOf', () => {
  it.each([
    ['Photographed book', 'photographed-book.bookreviver-profile.json'],
    ['  Old book / plates!  ', 'old-book-plates.bookreviver-profile.json'],
    ['Старая книга', 'старая-книга.bookreviver-profile.json'],
    ['???', 'profile.bookreviver-profile.json'],
  ])('names the file of %j as %s', (name, expected) => {
    expect(fileNameOf(name)).toBe(expected);
  });
});
