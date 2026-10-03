import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import type { ScanSchema } from '@/api';
import { stepChain, stepVersions } from '@/features/editors/chain';
import { popUndo, pushUndo, type UndoEntry } from '@/features/editors/history';
import { pictureOf } from '@/features/editors/picture';
import { isPlacement, pictureFor } from '@/features/editors/placement';
import { editsKey, useEditChanges, useEdits } from '@/features/editors/queries';
import { editableProcessorsOf, editorOf, hasEditor } from '@/features/editors/registry';
import type { EditorScene } from '@/features/editors/scene';
import type { EditorSession, StepChoice } from '@/features/editors/session';
import type { Geometry } from '@/features/editors/shapes';
import type { PageContext } from '@/features/editors/types';
import type { ImageSource } from '@/features/processing/compare';
import { useRunInFlight, useRunStage, useVersions } from '@/features/processing/queries';
import { readResult } from '@/features/processing/results';
import type { Processing } from '@/features/processing/useProcessing';
import { invalidateStageRows, invalidateStageSummary } from '@/features/projects/queries';
import { isTypingTarget } from '@/features/viewer/keys';
import { useActiveJobs } from '@/features/workspace/queries';
import type { StripItem } from '@/features/workspace/strip';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';

/**
 * The page editor of a stage: which processor offers one, whether it is open on the open page, the shape it shows, and
 * what saving, running again, "Auto" and Ctrl+Z do.
 *
 * A change reaches the server when the reader lets go of the shape: the edit is saved, and the stage is run on the one
 * page it belongs to, so the result is on the screen without another press. The run waits while another job of the book
 * is going, since the server runs one stage at a time, and asks once for any number of saves that came meanwhile.
 * "Auto" deletes the edit and runs again, and Ctrl+Z puts back the edit the page had before the last change, or deletes
 * the edit when it had none.
 */

/** The shape being moved, which stands in for the saved one until the server has the new one. */
interface Draft {
  key: string;
  /** The saved edit it was made from, as text; a draft is dropped when the saved edit is no longer that one. */
  base: string;
  geometry: Geometry;
}

/** A run that waits for the book to be free. */
interface WantedRun {
  recipeId: string;
  pageId: string;
}

export function useEditorSession({
  processing,
  current,
  items,
  scans,
  before,
}: {
  processing: Processing;
  current: StripItem | undefined;
  items: readonly StripItem[];
  scans: readonly ScanSchema[];
  /** The picture before the stage on the open page. */
  before: ImageSource | null;
}): EditorSession | null {
  const { projectId, stage, catalogue } = processing;
  const queryClient = useQueryClient();
  const { save, remove } = useEditChanges(projectId);
  const run = useRunStage(projectId, stage);
  const { mutate: startRun } = run;
  const activeJobs = useActiveJobs(projectId);

  // The processors are the ones the recipe runs by, since only its run reads an edit, and the reader works on one at a time
  const recipe = processing.recipe;
  const editable = editableProcessorsOf(recipe, catalogue);
  const [chosen, setChosen] = useState<string | null>(null);
  const processor = editable.find((entry) => entry.key === chosen) ?? editable[0];
  const kind = processor?.editor;
  const editor = kind !== undefined && hasEditor(kind) ? editorOf(kind) : undefined;
  const scan =
    current === undefined
      ? null
      : (scans.find((entry) => entry.id === current.page.scan_id) ?? null);

  // Each step of the recipe made a version, and a step reads the one before: an editor lies on what its step read and
  // starts from what its step found
  const head = current?.row?.version ?? null;
  const versions = useVersions(projectId, current?.page.id, stage);
  const chain = stepChain(versions.data ?? [], head);
  const found =
    processor === undefined ? { made: null, read: null } : stepVersions(chain, processor.key);
  const made = found.made ?? (editable.length === 1 ? head : null);
  const result = made === null ? null : readResult(made);
  const context: PageContext | undefined =
    current === undefined || processor === undefined
      ? undefined
      : { current, items, scan, stepInput: found.read, result, processorKey: processor.key };
  const owner = editor === undefined || context === undefined ? undefined : editor.owner(context);
  const picture =
    editor === undefined || current === undefined || processor === undefined
      ? null
      : pictureOf(
          pictureFor(editor.picture, processor.key),
          scan,
          current.page,
          before,
          found.read,
          made,
        );
  const available =
    processor !== undefined &&
    picture !== null &&
    (editor?.needsResult !== true || result !== null);

  const edits = useEdits(projectId, owner?.id, stage, available);
  const saved = edits?.find((entry) => entry.processor_key === processor?.key);
  const savedGeometry: Geometry | null = saved?.geometry ?? null;
  const savedText = JSON.stringify(savedGeometry);

  const [opened, setOpened] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [wanted, setWanted] = useState<WantedRun | null>(null);
  const undoStack = useRef<UndoEntry[]>([]);
  // What the server will hold once the writes in flight are done, which is what the next change replaces
  const written = useRef<{ key: string; geometry: Geometry | null } | null>(null);

  const key = `${owner?.id}|${processor?.key}`;
  const openKey = `${current?.page.id}|${stage}`;
  const active =
    editor !== undefined && picture !== null && (editor.alwaysOn || opened === openKey);

  // The run waits for the book to be free, and is asked for once whatever the number of saves before it
  const runInFlight = useRunInFlight(projectId);
  const idle = activeJobs.data !== undefined && activeJobs.data.length === 0 && !runInFlight;
  useEffect(() => {
    if (wanted === null || !idle) {
      return;
    }
    setWanted(null);
    startRun(
      {
        path: { project_id: projectId, stage },
        body: { recipe_id: wanted.recipeId, page_ids: [wanted.pageId] },
      },
      { onError: (failure) => setError(describeError(failure)) },
    );
  }, [wanted, idle, startRun, projectId, stage]);

  const write = async (next: Geometry | null, remember: boolean): Promise<void> => {
    if (
      editor === undefined ||
      context === undefined ||
      owner === undefined ||
      processor === undefined
    ) {
      return;
    }
    if (remember) {
      const previous = written.current?.key === key ? written.current.geometry : savedGeometry;
      undoStack.current = pushUndo(undoStack.current, {
        ownerId: owner.id,
        processorKey: processor.key,
        previous,
      });
    }
    written.current = { key, geometry: next };
    const path = {
      project_id: projectId,
      page_id: owner.id,
      stage,
      processor_key: processor.key,
    };
    try {
      if (next === null) {
        await remove.mutateAsync({ path });
      } else {
        await save.mutateAsync({
          path,
          body: { kind: processor.editor, geometry: JSON.stringify(next) },
        });
      }
      setError(null);
      if (editor.runsAfterEdit(context) && recipe !== undefined) {
        setWanted({ recipeId: recipe.id, pageId: owner.id });
      }
    } catch (failure) {
      written.current = null;
      setError(describeError(failure));
    } finally {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: editsKey(projectId, owner.id, stage) }),
        invalidateStageRows(queryClient, projectId, stage),
        invalidateStageSummary(queryClient, projectId),
      ]);
    }
  };

  const undo = (): void => {
    if (owner === undefined || processor === undefined) {
      return;
    }
    const taken = popUndo(undoStack.current, owner.id, processor.key);
    if (taken !== null) {
      undoStack.current = taken.rest;
      setDraft(null);
      void write(taken.entry.previous, false);
    }
  };

  const latestUndo = useRef(undo);
  useEffect(() => {
    latestUndo.current = undo;
  });
  useEffect(() => {
    if (!active) {
      return;
    }
    const onKeyDown = (event: KeyboardEvent): void => {
      const taken =
        (event.ctrlKey || event.metaKey) &&
        !event.shiftKey &&
        !event.altKey &&
        event.key.toLowerCase() === 'z';
      // A field keeps its own undo, and an open dialog keeps its keys
      if (!taken || isTypingTarget(event.target) || document.querySelector('[role="dialog"]')) {
        return;
      }
      event.preventDefault();
      latestUndo.current();
    };
    window.addEventListener('keydown', onKeyDown, { capture: true });
    return () => window.removeEventListener('keydown', onKeyDown, { capture: true });
  }, [active]);

  if (
    editor === undefined ||
    context === undefined ||
    processor === undefined ||
    picture === null ||
    !available
  ) {
    return null;
  }

  const fallback = editor.fallback({ ...context, size: editor.size(context) });
  const geometry =
    draft !== null && draft.key === key && draft.base === savedText
      ? draft.geometry
      : (savedGeometry ?? fallback);
  const hold = (next: Geometry): void => setDraft({ key, base: savedText, geometry: next });
  const commit = (next: Geometry): void => {
    hold(next);
    void write(next, true);
  };
  const saving = save.isPending || remove.isPending;
  const steps = editable.flatMap((entry): StepChoice[] => {
    if (!hasEditor(entry.editor)) {
      return [];
    }
    const stepVersion = stepVersions(chain, entry.key).made;
    const angle = stepVersion === null ? null : readResult(stepVersion).angle;
    return [
      {
        key: entry.key,
        title: isPlacement(entry.key)
          ? MESSAGES.editors.steps.placement
          : MESSAGES.editors.steps.kinds[entry.editor],
        manual: edits?.some((candidate) => candidate.processor_key === entry.key) ?? false,
        detail:
          entry.editor === 'rotation' && angle !== null
            ? MESSAGES.processing.thisPage.degrees(angle)
            : null,
        chosen: entry.key === processor.key,
      },
    ];
  });

  return {
    picture,
    alwaysOn: editor.alwaysOn,
    active,
    steps,
    choose: (stepKey: string) => {
      setChosen(stepKey);
      setDraft(null);
      setOpened(openKey);
    },
    hasEdit: savedGeometry !== null,
    busy: saving || wanted !== null || run.isPending,
    error,
    open: () => setOpened(openKey),
    close: () => setOpened(null),
    auto: () => {
      if (savedGeometry !== null) {
        setDraft({ key, base: savedText, geometry: fallback });
        void write(null, true);
      }
    },
    renderCanvas: (scene: EditorScene) => (
      <editor.Canvas
        scene={scene}
        geometry={geometry}
        size={editor.size(context)}
        context={context}
        onChange={hold}
        onCommit={commit}
      />
    ),
    renderPanel: () => (
      <editor.Panel
        geometry={geometry}
        processorKey={processor.key}
        disabled={saving}
        onCommit={commit}
      />
    ),
  };
}
