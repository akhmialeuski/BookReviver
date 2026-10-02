import { describe, expect, it } from 'vitest';
import type { MetadataSuggestionSchema } from '@/api';
import { toFields } from './fields';
import { projectWith } from './fixtures';
import { planSuggestion, suggestionSummary } from './suggestion';

/**
 * What using the suggestion of a file changes in the description.
 */

const NOTHING: MetadataSuggestionSchema = {
  title: '',
  contributors: [],
  publisher: '',
  publication_year: '',
  languages: [],
  identifiers: [],
  subjects: [],
};

const NAMES = {
  title: 'a title',
  contributors: 'contributors',
  publisher: 'a publisher',
  publication_year: 'a year',
  languages: 'languages',
  identifiers: 'identifiers',
  subjects: 'subjects',
};

describe('planSuggestion', () => {
  it('changes nothing for a suggestion that is empty', () => {
    const plan = planSuggestion(toFields(projectWith()), NOTHING);

    expect(plan.changes).toEqual({});
    expect(plan.rows).toEqual([]);
  });

  it('changes nothing for values the description already has', () => {
    const current = toFields(
      projectWith({ title: 'Notes', publisher: 'Suvorin', languages: ['rus'] }),
    );

    const plan = planSuggestion(current, {
      ...NOTHING,
      title: 'Notes',
      publisher: 'Suvorin',
      languages: ['rus'],
    });

    expect(plan.rows).toEqual([]);
  });

  it('replaces a single value that differs, the title included', () => {
    const current = toFields(projectWith({ title: 'Zapiski', publisher: 'Suvorin' }));

    const plan = planSuggestion(current, {
      ...NOTHING,
      title: 'Записки',
      publisher: 'Suvorin',
      publication_year: '1894',
    });

    expect(plan.changes).toEqual({ title: 'Записки', publication_year: '1894' });
    expect(plan.rows).toEqual([
      { field: 'title', text: 'Записки' },
      { field: 'publication_year', text: '1894' },
    ]);
  });

  it('adds the entries a list lacks and keeps the entries it has', () => {
    const current = toFields(
      projectWith({
        contributors: [{ name: 'Sokolov', role: 'aut' }],
        subjects: ['Prose'],
      }),
    );

    const plan = planSuggestion(current, {
      ...NOTHING,
      contributors: [
        { name: 'Sokolov', role: 'aut' },
        { name: 'Ivanov', role: 'edt' },
      ],
      subjects: ['Prose'],
      languages: ['rus'],
      identifiers: [{ scheme: 'url', value: 'https://example.org/book' }],
    });

    expect(plan.changes.contributors).toEqual([
      { name: 'Sokolov', role: 'aut' },
      { name: 'Ivanov', role: 'edt' },
    ]);
    expect(plan.changes.languages).toEqual(['rus']);
    expect(plan.changes.identifiers).toEqual([
      { scheme: 'url', value: 'https://example.org/book' },
    ]);
    expect(plan.changes).not.toHaveProperty('subjects');
    expect(plan.rows.map((row) => row.field)).toEqual(['contributors', 'languages', 'identifiers']);
    expect(plan.rows[0]?.text).toBe('Ivanov');
  });
});

describe('suggestionSummary', () => {
  it('lists the changed fields the way a sentence does', () => {
    const rows = [
      { field: 'title', text: 'x' },
      { field: 'contributors', text: 'x' },
      { field: 'publication_year', text: 'x' },
    ] as const;

    expect(suggestionSummary(rows, NAMES)).toBe('a title, contributors and a year');
    expect(suggestionSummary(rows.slice(0, 2), NAMES)).toBe('a title and contributors');
    expect(suggestionSummary(rows.slice(0, 1), NAMES)).toBe('a title');
  });
});
