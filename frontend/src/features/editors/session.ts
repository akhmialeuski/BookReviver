import type { ReactNode } from 'react';
import type { EditorScene } from '@/features/editors/scene';
import type { ImageSource } from '@/features/processing/compare';

/** One step of the recipe of a stage that has an editor, as the reader picks it from the list in the panel. */
export interface StepChoice {
  /** The key of the processor of the step. */
  key: string;
  /** What the editor of the step sets, in the reader's words. */
  title: string;
  /** Whether the reader gave the step an edit of their own, and not the step found the result by itself. */
  manual: boolean;
  /** What the step found on the open page, such as the angle, or null when there is nothing to say. */
  detail: string | null;
  /** Whether this is the editor that is shown. */
  chosen: boolean;
}

/**
 * What the screen sees of an open page editor: whether it is open, the picture the canvas has to show for it, and the
 * pieces it draws on the canvas and in the panel. Everything about the shape it edits stays inside it.
 */
export interface EditorSession {
  /** The picture the canvas shows while the editor is open. */
  picture: ImageSource;
  /** Whether the editor is open whenever the stage is, and so has no "Set by hand" to press. */
  alwaysOn: boolean;
  /** Whether the editor is open now. */
  active: boolean;
  /** The steps of the stage that have an editor, in the order of the recipe; the editor shown is one of them. */
  steps: readonly StepChoice[];
  /** Show the editor of another step, and open it. */
  choose: (stepKey: string) => void;
  /** Whether the page has a manual edit saved for the step that is shown. */
  hasEdit: boolean;
  /** Whether a change is being saved or the page is waiting to be run again. */
  busy: boolean;
  /** Why the last change could not be saved or run, or null. */
  error: string | null;
  open: () => void;
  close: () => void;
  /** Delete the edit and run the stage on the page again. */
  auto: () => void;
  /** Draw the editor over the canvas. */
  renderCanvas: (scene: EditorScene) => ReactNode;
  /** Draw the part of the editor that lives in the panel. */
  renderPanel: () => ReactNode;
}
