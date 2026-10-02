import type { ReactNode } from 'react';
import type { EditorScene } from '@/features/editors/scene';
import type { ImageSource } from '@/features/processing/compare';

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
  /** Whether the page has a manual edit saved. */
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
