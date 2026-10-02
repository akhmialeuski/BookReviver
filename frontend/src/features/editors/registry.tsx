import type { EditorKind } from '@/api';
import { lineEditor } from '@/features/editors/lineEditor';
import { rotationEditor } from '@/features/editors/rotationEditor';
import type { EditableKind, EditorShapes } from '@/features/editors/shapes';
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
};

/** Tell whether the kind of editor of a processor has a component. */
export function hasEditor(kind: EditorKind): kind is EditableKind {
  return Object.hasOwn(EDITORS, kind);
}

/** Give the editor of a kind that has a component. */
export function editorOf(kind: EditableKind): RegisteredEditor {
  return EDITORS[kind];
}
