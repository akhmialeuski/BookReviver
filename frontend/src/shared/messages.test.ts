import { describe, expect, it } from 'vitest';
import { MESSAGES } from '@/shared/messages';

/** The sentences the interface builds from its parts are made here, so a component never joins them itself. */

describe('MESSAGES', () => {
  const menu = MESSAGES.processing.runMenu;

  it('writes the button of the run from what the pages already have, the pages and the step it goes through', () => {
    expect(menu.button(false, '2 selected pages', 'Deskew')).toBe(
      'Run on 2 selected pages through Deskew',
    );
    expect(menu.button(true, 'this page', 'Crop')).toBe('Run again on this page through Crop');
  });

  it('names a group of pages of one kind in the menu of the run', () => {
    expect(menu.groupOf('Text')).toBe('Pages of a kind · Text');
  });

  it('says what the button that adds a step does and what it is for, in one title', () => {
    const catalogue = MESSAGES.workspace.steps.catalogue;

    expect(catalogue.openHint).toBe(`${catalogue.open}. ${catalogue.hint}`);
  });

  it('marks a step of a profile that is off, and names the page a delete button belongs to', () => {
    expect(MESSAGES.profiles.library.steps.titleOff('Deskew')).toBe('Deskew (off)');
    expect(MESSAGES.pages.edit.removeNamed('p. 3')).toBe('Delete page: p. 3');
  });
});
