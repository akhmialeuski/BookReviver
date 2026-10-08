import type { ComponentType } from 'react';
import type { FigureState, PageSchema, ScanSchema } from '@/api';
import type { EditorScene } from '@/features/editors/scene';
import type { Geometry, RectShape, Size } from '@/features/editors/shapes';
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
  /** What the step of the editor found on the open page, or null when it has not run. */
  result: PageResult | null;
  /** The key of the processor of the step the editor sets. */
  processorKey: string;
  /**
   * The size in pixels of the picture the editor lies on, as the picture says it, or null when it is not known yet or the
   * picture has no pyramid to say it. An editor whose step has not run starts from the whole picture, so it needs it.
   */
  pictureSize: Size | null;
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
  /** The state the shape is in on the page, which decides how it is drawn. */
  figure: FigureState;
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
  /** The settings of the step the editor sets, as the recipe saved them. */
  params: Readonly<Record<string, unknown>>;
  /** Whether a change cannot be made now, such as while the last one is being saved. */
  disabled: boolean;
  /** The size of the picture in the pixels of the edit, or null when the editor counts in the picture's own. */
  size: Size | null;
  /** The shape changed while the reader is still moving it, such as a slider that has not been let go. */
  onChange: (shape: S) => void;
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
  /**
   * The rectangle the editor draws beyond the picture, in the pixels of the edit, which the fit of the canvas holds too so
   * that its handles can be reached; absent for an editor that draws only on the picture.
   */
  reach?: (context: PageContext) => RectShape | null;
  /** The shape to start from when the page has no edit. */
  fallback: (context: FallbackContext) => S;
  read: (geometry: Geometry | null) => S | null;
  write: (shape: S) => Geometry;
  /** Put a shape of the edit into words for the history of the step, with the pixels of the page rounded. */
  describe: (shape: S) => string;
  /**
   * What the history says of an edit whose geometry does not fit the shape, for an editor that can say something true
   * without it, such as the brush, whose mask stands whether or not the strokes were kept. Absent for the others, whose
   * edits then read "Set by hand".
   */
  describeUnfit?: string;
  /**
   * Paint the mask the server keeps beside the shape, for an editor whose edit is a mask, such as the brush: a white
   * picture of the size of the edit's image where the reader brushed, and black elsewhere.
   */
  mask?: (shape: S, size: Size) => Promise<Blob>;
  Canvas: ComponentType<CanvasProps<S>>;
  /** The part in the panel, or absent for an editor that has nothing there beyond the shape on the canvas. */
  Panel?: ComponentType<PanelProps<S>>;
}

/** What the canvas part of a registered editor gets: the geometry of the edit in place of a typed shape. */
export interface GeometryCanvasProps {
  scene: EditorScene;
  geometry: Geometry;
  size: Size | null;
  context: PageContext;
  figure: FigureState;
  onChange: (geometry: Geometry) => void;
  onCommit: (geometry: Geometry) => void;
}

/** What the part of a registered editor in the panel gets. */
export interface GeometryPanelProps {
  geometry: Geometry;
  processorKey: string;
  params: Readonly<Record<string, unknown>>;
  disabled: boolean;
  size: Size | null;
  onChange: (geometry: Geometry) => void;
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
  /** The rectangle the editor draws beyond the picture, in the pixels of the edit, or null for none. */
  reach: (context: PageContext) => RectShape | null;
  /** The geometry to start from when the page has no edit. */
  fallback: (context: FallbackContext) => Geometry;
  /** Put the geometry of an edit into words, which is "Set by hand" when it does not fit the shape the editor draws. */
  describe: (geometry: Geometry | null) => string;
  /** Paint the mask that is saved with the geometry, for an editor whose edit is a mask; absent for the others. */
  mask: ((geometry: Geometry, size: Size) => Promise<Blob>) | null;
  Canvas: ComponentType<GeometryCanvasProps>;
  Panel: ComponentType<GeometryPanelProps>;
}
