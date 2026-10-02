import type { ComponentType } from 'react';
import type { PageSchema, PageVersionSchema, ScanSchema } from '@/api';
import type { EditorScene } from '@/features/editors/scene';
import type { Geometry, Size } from '@/features/editors/shapes';
import type { PageResult } from '@/features/processing/results';
import type { StripItem } from '@/features/workspace/strip';

/**
 * What an editor is made of, and what the registry holds for it.
 *
 * An editor is written against its own typed shape, `S`. The registry wraps it so that the rest of the screen handles
 * only the JSON of an edit, which is what the server stores and what the undo remembers, and never needs to know which
 * shape an editor draws.
 */

/** Which picture the editor lies on. */
export const Picture = {
  /** The scan the page was cut from, in the pixels of the scan. */
  Scan: 'scan',
  /** The picture the step reads, which is the result of the stage before. */
  Input: 'input',
  /** The picture the step itself made, which is the page of the book for the step that places a block on it. */
  Output: 'output',
} as const;

/** One kind of picture (derived from {@link Picture}). */
export type Picture = (typeof Picture)[keyof typeof Picture];

/** The open page and the pages around it in the strip. */
export interface PageContext {
  current: StripItem;
  items: readonly StripItem[];
  /** The scan the open page was cut from, or null for a page without one. */
  scan: ScanSchema | null;
  /** The version the step of the editor read, or null when the step reads the picture before the stage. */
  stepInput: PageVersionSchema | null;
  /** What the step of the editor found on the open page, or null when it has not run. */
  result: PageResult | null;
  /** The key of the processor of the step the editor sets. */
  processorKey: string;
}

/** What an editor needs to start from when the page has no edit. */
export interface FallbackContext extends PageContext {
  /** The size of the picture in the pixels of the edit, or null when the editor counts in the picture's own. */
  size: Size | null;
}

/** What the canvas part of an editor gets. */
export interface CanvasProps<S> {
  scene: EditorScene;
  shape: S;
  /** The size of the picture in the pixels of the edit, or null when the editor counts in the picture's own. */
  size: Size | null;
  context: PageContext;
  /** The shape changed while the reader is still moving it. */
  onChange: (shape: S) => void;
  /** The reader let go of the shape, so it is to be saved. */
  onCommit: (shape: S) => void;
}

/** What the part of an editor in the panel gets. */
export interface PanelProps<S> {
  shape: S;
  /** The key of the processor of the step the editor sets. */
  processorKey: string;
  /** Whether a change cannot be made now, such as while the last one is being saved. */
  disabled: boolean;
  onCommit: (shape: S) => void;
}

/** An editor as its author writes it, against the typed shape it draws. */
export interface EditorDefinition<S> {
  picture: Picture;
  /** Whether the editor is open whenever its stage is, and not only after "Set by hand". */
  alwaysOn: boolean;
  /** Whether the editor is offered only once its step has run on the page, since it starts from what the step found. */
  needsResult: boolean;
  /** The page the edit belongs to, which is not always the open one. */
  owner: (context: PageContext) => PageSchema;
  /** The size of the picture in the pixels of the edit, or null for the picture's own. */
  size: (context: PageContext) => Size | null;
  /** Whether saving an edit is followed by running the stage on the page. */
  runsAfterEdit: (context: PageContext) => boolean;
  /** The shape to start from when the page has no edit. */
  fallback: (context: FallbackContext) => S;
  read: (geometry: Geometry | null) => S | null;
  write: (shape: S) => Geometry;
  Canvas: ComponentType<CanvasProps<S>>;
  Panel: ComponentType<PanelProps<S>>;
}

/** What the canvas part of a registered editor gets: the geometry of the edit in place of a typed shape. */
export interface GeometryCanvasProps {
  scene: EditorScene;
  geometry: Geometry;
  size: Size | null;
  context: PageContext;
  onChange: (geometry: Geometry) => void;
  onCommit: (geometry: Geometry) => void;
}

/** What the part of a registered editor in the panel gets. */
export interface GeometryPanelProps {
  geometry: Geometry;
  processorKey: string;
  disabled: boolean;
  onCommit: (geometry: Geometry) => void;
}

/** A registered editor, with the shape it draws hidden inside it. */
export interface RegisteredEditor {
  picture: Picture;
  alwaysOn: boolean;
  needsResult: boolean;
  owner: (context: PageContext) => PageSchema;
  size: (context: PageContext) => Size | null;
  runsAfterEdit: (context: PageContext) => boolean;
  /** The geometry to start from when the page has no edit. */
  fallback: (context: FallbackContext) => Geometry;
  Canvas: ComponentType<GeometryCanvasProps>;
  Panel: ComponentType<GeometryPanelProps>;
}
