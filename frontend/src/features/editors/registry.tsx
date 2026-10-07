import type { ProcessorSchema, RecipeSchema, StepSchema } from '@/api';
import { brushEditor } from '@/features/editors/brushEditor';
import { lineEditor } from '@/features/editors/lineEditor';
import { marginsEditor } from '@/features/editors/marginsEditor';
import { meshEditor } from '@/features/editors/meshEditor';
import { quadEditor } from '@/features/editors/quadEditor';
import { rectEditor } from '@/features/editors/rectEditor';
import { regionsEditor } from '@/features/editors/regionsEditor';
import { rotationEditor } from '@/features/editors/rotationEditor';
import { type EditableKind, type EditorShapes, recordOf } from '@/features/editors/shapes';
import { splitEditor } from '@/features/editors/splitEditor';
import type {
  EditorDefinition,
  GeometryCanvasProps,
  GeometryPanelProps,
  RegisteredEditor,
} from '@/features/editors/types';
import { MESSAGES } from '@/shared/messages';

/**
 * The registry of page editors: for each kind of editor a processor offers, the component that edits it.
 *
 * An editor is written against its own typed shape. Registering it wraps the definition so that the screen deals only in
 * the geometry of an edit, the JSON the server stores, and the shape never leaks out of the editor. A new editor adds
 * its shape to `EditorShapes`, writes its definition and adds one line below; the type of `EDITORS` stops compiling until
 * every kind of `EditorShapes` has an entry, and the entry of a kind has to draw that kind's shape.
 */

/** Wrap the definition of one editor into what the registry holds. */
function register<K extends EditableKind>(
  definition: EditorDefinition<EditorShapes[K]>,
): RegisteredEditor {
  function Canvas(props: GeometryCanvasProps): React.JSX.Element | null {
    const shape = definition.read(props.geometry);
    return shape === null ? null : (
      <definition.Canvas
        scene={props.scene}
        shape={shape}
        size={props.size}
        context={props.context}
        figure={props.figure}
        onChange={(next) => props.onChange(definition.write(next))}
        onCommit={(next) => props.onCommit(definition.write(next))}
      />
    );
  }
  function Panel(props: GeometryPanelProps): React.JSX.Element | null {
    const shape = definition.read(props.geometry);
    return shape === null ? null : (
      <definition.Panel
        shape={shape}
        processorKey={props.processorKey}
        params={props.params}
        disabled={props.disabled}
        size={props.size}
        onChange={(next) => props.onChange(definition.write(next))}
        onCommit={(next) => props.onCommit(definition.write(next))}
      />
    );
  }
  const { mask } = definition;
  return {
    picture: definition.picture,
    alwaysOn: definition.alwaysOn,
    needsResult: definition.needsResult,
    owner: definition.owner,
    size: definition.size,
    runsAfterEdit: definition.runsAfterEdit,
    reach: definition.reach ?? (() => null),
    fallback: (context) => definition.write(definition.fallback(context)),
    describe: (geometry) => {
      const shape = definition.read(geometry);
      return shape === null
        ? (definition.describeUnfit ?? MESSAGES.processing.timeline.hand.unknown)
        : definition.describe(shape);
    },
    mask:
      mask === undefined
        ? null
        : async (geometry, size) => {
            const shape = definition.read(geometry);
            if (shape === null) {
              throw new Error(MESSAGES.editors.unfit);
            }
            return mask(shape, size);
          },
    Canvas,
    Panel,
  };
}

const EDITORS: Readonly<Record<EditableKind, RegisteredEditor>> = {
  line: register<'line'>(lineEditor),
  rotation: register<'rotation'>(rotationEditor),
  split: register<'split'>(splitEditor),
  quad: register<'quad'>(quadEditor),
  rect: register<'rect'>(rectEditor),
  mesh: register<'mesh'>(meshEditor),
  regions: register<'regions'>(regionsEditor),
  'brush-mask': register<'brush-mask'>(brushEditor),
  'content-box': register<'content-box'>(marginsEditor),
};

/** Tell whether the kind of editor of a processor has a component. */
export function hasEditor(kind: string): kind is EditableKind {
  return Object.hasOwn(EDITORS, kind);
}

/** A step of the recipe that has an editor, with the processor it runs and its place in the recipe. */
export interface EditableStep {
  step: StepSchema;
  /** Index of the step in the recipe, from zero. */
  index: number;
  processor: ProcessorSchema;
  /** The kind of editor the processor offers, which has a component. */
  kind: EditableKind;
}

/**
 * Find the steps whose editors a stage shows: the enabled steps of the recipe whose processor offers an editor that has
 * a component, in the order of the recipe. A recipe that runs a processor twice gives both steps, and each has its own
 * edits.
 *
 * The recipe decides, and not the catalogue, since the catalogue lists the processors a stage could use, such as the
 * cutting of a spread by a line and the automatic split, which read different edits and are not both in the recipe.
 *
 * @param recipe The recipe the stage runs by, or undefined while the recipes are being read.
 * @param catalogue The processors of the stage.
 * @returns The steps, none when the recipe has no step with an editor.
 */
export function editableStepsOf(
  recipe: Pick<RecipeSchema, 'steps'> | undefined,
  catalogue: readonly ProcessorSchema[],
): EditableStep[] {
  return (recipe?.steps ?? []).flatMap((step, index) => {
    const processor = step.enabled
      ? catalogue.find((candidate) => candidate.key === step.processor_key)
      : undefined;
    return processor !== undefined && hasEditor(processor.editor)
      ? [{ step, index, processor, kind: processor.editor }]
      : [];
  });
}

/** Give the editor of a kind that has a component. */
export function editorOf(kind: EditableKind): RegisteredEditor {
  return EDITORS[kind];
}

/**
 * Put a manual edit into words, by the kind of editor that made it.
 *
 * The kind and the geometry are what the server stores for the hand layer, so neither is trusted to be one an editor
 * knows. Pixels of the page are rounded to whole numbers, and an angle to one decimal.
 *
 * @param kind The kind of the editor that made the edit.
 * @param geometry The shape the editor drew, as the server stored it.
 * @returns The text, which is "Set by hand" for a kind that has no editor or a geometry that does not fit its kind.
 */
export function describeEdit(kind: string, geometry: unknown): string {
  return hasEditor(kind)
    ? EDITORS[kind].describe(recordOf(geometry))
    : MESSAGES.processing.timeline.hand.unknown;
}
