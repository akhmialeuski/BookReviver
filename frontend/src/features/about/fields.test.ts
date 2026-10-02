import { describe, expect, it } from 'vitest';
import {
  type EditableFields,
  FIELD_KEYS,
  type FieldKey,
  hasChanges,
  isEqual,
  type Problem,
  parseCodes,
  parseHeight,
  parseLines,
  planSave,
  toFields,
} from './fields';
import { projectWith } from './fixtures';

/**
 * What the About tab sends for the changes made so far, and how it reads the text of its list fields.
 */

const SAVED = toFields(projectWith());

describe('toFields', () => {
  it('holds every editable field once and leaves out the derived author', () => {
    const fields = toFields(projectWith({ primary_author: 'Yanka Kupala' }));

    expect(Object.keys(fields).sort()).toEqual([...FIELD_KEYS].sort());
    expect(fields).not.toHaveProperty('primary_author');
  });

  it('takes the image policy and the cover from the book itself', () => {
    const project = {
      ...projectWith(),
      image_policy: 'lossless',
      cover_page_id: 'page-3',
    } as const;

    expect(toFields(project)).toMatchObject({ image_policy: 'lossless', cover_page_id: 'page-3' });
  });
});

describe('planSave', () => {
  it('sends nothing when nothing was changed', () => {
    const plan = planSave(SAVED, {});

    expect(hasChanges(plan)).toBe(false);
    expect(plan.problems).toEqual({});
  });

  it('sends only the fields that differ from the saved ones', () => {
    const plan = planSave(SAVED, { title: 'A title', subtitle: 'Tales', publisher: '' });

    expect(plan.patch).toEqual({ subtitle: 'Tales' });
  });

  it('sends a field the person emptied as an empty value, which clears it', () => {
    const saved = toFields(projectWith({ subtitle: 'Tales', height_cm: 22 }));

    expect(planSave(saved, { subtitle: '', height_cm: null }).patch).toEqual({
      subtitle: '',
      height_cm: null,
    });
  });

  it('sends a list whole when an entry of it changed', () => {
    const saved = toFields(projectWith({ contributors: [{ name: 'Ivan', role: 'aut' }] }));
    const same = planSave(saved, { contributors: [{ role: 'aut', name: 'Ivan' }] });
    const edited = planSave(saved, {
      contributors: [
        { name: 'Ivan', role: 'aut' },
        { name: 'Petr', role: 'edt' },
      ],
    });

    expect(hasChanges(same)).toBe(false);
    expect(edited.patch.contributors).toHaveLength(2);
  });

  it('sends the choices of the image policy and of the cover with the description', () => {
    const plan = planSave(SAVED, { image_policy: 'lossless', cover_page_id: 'page-2' });

    expect(plan.patch).toEqual({ image_policy: 'lossless', cover_page_id: 'page-2' });
  });

  it('tells one set of changes from another by its key', () => {
    const first = planSave(SAVED, { subtitle: 'Tales' });

    expect(first.key).toBe(planSave(SAVED, { subtitle: 'Tales' }).key);
    expect(first.key).not.toBe(planSave(SAVED, { subtitle: 'Tale' }).key);
  });

  const refused: [string, Partial<EditableFields>, FieldKey, Problem][] = [
    ['an empty title', { title: '   ' }, 'title', 'title-empty'],
    ['a language code of two letters', { languages: ['ru'] }, 'languages', 'language-code'],
    ['a language code with capitals', { languages: ['RUS'] }, 'languages', 'language-code'],
    ['a height of zero', { height_cm: 0 }, 'height_cm', 'height-range'],
    ['a height above the limit', { height_cm: 201 }, 'height_cm', 'height-range'],
    ['a height that is no whole number', { height_cm: 21.5 }, 'height_cm', 'height-range'],
    [
      'a contributor without a name',
      { contributors: [{ name: ' ', role: 'aut' }] },
      'contributors',
      'contributor-name',
    ],
    [
      'an identifier without a value',
      { identifiers: [{ scheme: 'isbn', value: '' }] },
      'identifiers',
      'identifier-value',
    ],
  ];

  it.each(refused)(
    'keeps %s out of the patch and names the problem',
    (_name, edits, field, problem) => {
      const plan = planSave(SAVED, edits);

      expect(plan.patch).not.toHaveProperty(field);
      expect(plan.problems).toEqual({ [field]: problem });
    },
  );

  it('still sends the valid fields beside a field that cannot be sent', () => {
    const plan = planSave(SAVED, { subtitle: 'Tales', languages: ['ru'] });

    expect(plan.patch).toEqual({ subtitle: 'Tales' });
    expect(plan.problems).toEqual({ languages: 'language-code' });
  });

  it('accepts the edges of the height range', () => {
    expect(planSave(SAVED, { height_cm: 1 }).problems).toEqual({});
    expect(planSave(SAVED, { height_cm: 200 }).problems).toEqual({});
  });
});

describe('isEqual', () => {
  it('compares lists entry by entry and rows by their members, whatever the order of the members', () => {
    expect(isEqual(['a', 'b'], ['a', 'b'])).toBe(true);
    expect(isEqual(['a', 'b'], ['b', 'a'])).toBe(false);
    expect(isEqual({ name: 'Ivan', role: 'aut' }, { role: 'aut', name: 'Ivan' })).toBe(true);
    expect(isEqual({ name: 'Ivan', role: 'aut' }, { name: 'Ivan', role: 'edt' })).toBe(false);
  });

  it('tells null from an empty value', () => {
    expect(isEqual(null, '')).toBe(false);
    expect(isEqual(null, null)).toBe(true);
  });
});

describe('parseLines', () => {
  it('gives one entry per line without blank lines and spaces', () => {
    expect(parseLines('  Folk songs \n\n Tales\n')).toEqual(['Folk songs', 'Tales']);
  });

  it('gives nothing for an empty text', () => {
    expect(parseLines('')).toEqual([]);
  });
});

describe('parseCodes', () => {
  it('splits on spaces, commas and semicolons and lowers the case', () => {
    expect(parseCodes('RUS, bel;pol  ')).toEqual(['rus', 'bel', 'pol']);
  });

  it('keeps a code once', () => {
    expect(parseCodes('rus rus bel')).toEqual(['rus', 'bel']);
  });
});

describe('parseHeight', () => {
  it('reads a number and takes an empty field for no height', () => {
    expect(parseHeight('22')).toBe(22);
    expect(parseHeight('  ')).toBeNull();
  });
});
