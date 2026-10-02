import type { EditorKind, ProcessorSchema, RecipeSchema } from '@/api';
import { lineEditor } from '@/features/editors/lineEditor';
import { rotationEditor } from '@/features/editors/rotationEditor';
import type { EditableKind, EditorShapes } from '@/features/editors/shapes';
import { splitEditor } from '@/features/editors/splitEditor';
import type {
  EditorDefinition,
  GeometryCanvasProps,
  GeometryPanelProps,
  RegisteredEditor,
} from '@/features/editors/types';

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
        disabled={props.disabled}
        onCommit={(next) => props.onCommit(definition.write(next))}
      />
    );
  }
  return {
    picture: definition.picture,
    alwaysOn: definition.alwaysOn,
    owner: definition.owner,
    size: definition.size,
    runsAfterEdit: definition.runsAfterEdit,
    fallback: (context) => definition.write(definition.fallback(context)),
    Canvas,
    Panel,
  };
}

const EDITORS: Readonly<Record<EditableKind, RegisteredEditor>> = {
  line: register<'line'>(lineEditor),
  rotation: register<'rotation'>(rotationEditor),
  split: register<'split'>(splitEditor),
};

/** Tell whether the kind of editor of a processor has a component. */
export function hasEditor(kind: EditorKind): kind is EditableKind {
  return Object.hasOwn(EDITORS, kind);
}

/**
 * Find the processor whose editor a stage shows: the first enabled step of the recipe whose processor offers an editor
 * that has a component.
 *
 * The recipe decides, and not the catalogue, since the catalogue lists the processors a stage could use, such as the
 * cutting of a spread by a line and the automatic split, which read different edits and are not both in the recipe.
 *
 * @param recipe The recipe the stage runs by, or undefined while the recipes are being read.
 * @param catalogue The processors of the stage.
 * @returns The processor, or undefined when the recipe has no step with an editor.
 */
export function editedProcessorOf(
  recipe: Pick<RecipeSchema, 'steps'> | undefined,
  catalogue: readonly ProcessorSchema[],
): ProcessorSchema | undefined {
  for (const step of recipe?.steps ?? []) {
    const entry = step.enabled
      ? catalogue.find((candidate) => candidate.key === step.processor_key)
      : undefined;
    if (entry !== undefined && hasEditor(entry.editor)) {
      return entry;
    }
  }
  return undefined;
}

/** Give the editor of a kind that has a component. */
export function editorOf(kind: EditableKind): RegisteredEditor {
  return EDITORS[kind];
}
