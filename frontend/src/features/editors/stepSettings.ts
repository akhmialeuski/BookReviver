import { createContext, useContext } from 'react';

/**
 * The settings of the step of the open editor on the open page, which an editor that sets more than its shape reads and
 * changes: the Margins editor moves the sides of the border of the page and picks the alignment, and both are settings of
 * the page for the step and not a part of the edit.
 */

/** What an editor reads and sets of the settings the open page has for its step. */
export interface StepSettings {
  /** The settings the step runs with on the open page: those of the recipe, with the fields the page changes on top. */
  values: Readonly<Record<string, unknown>>;
  /** Set the value one field has on the open page, which runs the stage on the page again. */
  set: (name: string, value: unknown) => void;
  /** Whether a change is being saved or the page is waiting to be run again. */
  busy: boolean;
}

export const StepSettingsContext = createContext<StepSettings | null>(null);

/** Read the settings of the open step on the open page, or none outside an editor that gives them. */
export function useStepSettings(): StepSettings | null {
  return useContext(StepSettingsContext);
}
