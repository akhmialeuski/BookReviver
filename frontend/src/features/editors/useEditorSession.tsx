import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import type { ScanSchema } from '@/api';
import { stepChain, stepVersions } from '@/features/editors/chain';
import { popUndo, pushUndo, type UndoEntry } from '@/features/editors/history';
import { pictureOf } from '@/features/editors/picture';
import { isPlacement, pictureFor } from '@/features/editors/placement';
import { editsKey, useEditChanges, useEdits } from '@/features/editors/queries';
import {
  type EditableStep,
  editableStepsOf,
  editorOf,
  hasEditor,
} from '@/features/editors/registry';
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
import { describeError, ProblemError } from '@/shared/http/problem';
import { HttpStatus } from '@/shared/http/status';
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

/** The size an editor that paints a mask is given when the step has not said how large its picture is. */
const EMPTY_SIZE = { width: 0, height: 0 };

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
  focusStepId,
}: {
  processing: Processing;
  current: StripItem | undefined;
  items: readonly StripItem[];
  scans: readonly ScanSchema[];
  /** The picture before the stage on the open page. */
  before: ImageSource | null;
  /**
   * The step open in the step workspace, whose editor is the one shown, or none when that step has no editor. Absent when
   * no step is open, and the reader then picks from the steps that have one.
   */
  focusStepId?: string;
}): EditorSession | null {
  const { projectId, stage, catalogue } = processing;
  const queryClient = useQueryClient();
  const { save, remove } = useEditChanges(projectId);
  const run = useRunStage(projectId, stage);
  const { mutate: startRun } = run;
  const activeJobs = useActiveJobs(projectId);

  // The steps are the ones the recipe runs by, since only its run reads an edit, and the reader works on one at a time.
  // An edit belongs to a step and not to its processor, so a recipe that runs one twice has two editors
  const recipe = processing.recipe;
  const editable = editableStepsOf(recipe, catalogue);
  const [chosen, setChosen] = useState<string | null>(null);
  const entry =
    focusStepId === undefined
      ? (editable.find((candidate) => candidate.step.step_id === chosen) ?? editable[0])
      : editable.find((candidate) => candidate.step.step_id === focusStepId);
  const processor = entry?.processor;
  const step = entry?.step;
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
    entry === undefined || recipe === undefined
      ? { made: null, read: null }
      : stepVersions(chain, recipe.steps, entry.index);
  // A page that did not meet the condition of the step passed it as it was, so the step found nothing on it
  const skipped = found.made?.data.skipped_by_condition === true;
  const made = skipped ? null : (found.made ?? (editable.length === 1 ? head : null));
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
    !skipped &&
    (editor?.needsResult !== true || result !== null);

  const edits = useEdits(projectId, owner?.id, stage, available);
  const saved = edits?.find((candidate) => candidate.step_id === step?.step_id);
  const savedGeometry: Geometry | null = saved?.geometry ?? null;
  const savedText = JSON.stringify(savedGeometry);

  const [opened, setOpened] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [wanted, setWanted] = useState<WantedRun | null>(null);
  const undoStack = useRef<UndoEntry[]>([]);
  // What the server will hold once the writes in flight are done, which is what the next change replaces
  const written = useRef<{ key: string; geometry: Geometry | null } | null>(null);

  const key = `${owner?.id}|${step?.step_id}`;
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
      {
        onError: (failure) => {
          if (failure instanceof ProblemError && failure.status === HttpStatus.Conflict) {
            // A job the book queued after the last one, such as the clearing of old results, is not on the list of
            // active jobs yet, so the run waits for the next quiet moment instead of being lost
            setWanted((newer) => newer ?? { ...wanted });
          } else {
            setError(describeError(failure));
          }
        },
      },
    );
  }, [wanted, idle, startRun, projectId, stage]);

  const write = async (next: Geometry | null, remember: boolean): Promise<void> => {
    if (
      editor === undefined ||
      context === undefined ||
      owner === undefined ||
      processor === undefined ||
      step === undefined
    ) {
      return;
    }
    if (remember) {
      const previous = written.current?.key === key ? written.current.geometry : savedGeometry;
      undoStack.current = pushUndo(undoStack.current, {
        ownerId: owner.id,
        stepId: step.step_id,
        previous,
      });
    }
    written.current = { key, geometry: next };
    const path = {
      project_id: projectId,
      page_id: owner.id,
      stage,
      step_id: step.step_id,
    };
    try {
      if (next === null) {
        await remove.mutateAsync({ path });
      } else {
        // An editor whose edit is a mask paints it from the shape, and the server keeps both
        const size = editor.size(context);
        const mask = editor.mask === null ? undefined : await editor.mask(next, size ?? EMPTY_SIZE);
        await save.mutateAsync({
          path,
          body: { kind: processor.editor, geometry: JSON.stringify(next), mask },
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
    if (owner === undefined || step === undefined) {
      return;
    }
    const taken = popUndo(undoStack.current, owner.id, step.step_id);
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
  const titleOf = (candidate: EditableStep): string =>
    isPlacement(candidate.processor.key)
      ? MESSAGES.editors.steps.placement
      : MESSAGES.editors.steps.kinds[candidate.kind];
  const steps = editable.flatMap((candidate): StepChoice[] => {
    if (recipe === undefined) {
      return [];
    }
    const stepVersion = stepVersions(chain, recipe.steps, candidate.index).made;
    const angle =
      stepVersion === null || stepVersion.data.skipped_by_condition === true
        ? null
        : readResult(stepVersion).angle;
    const title = titleOf(candidate);
    // Two steps of one kind of editor are told apart by their place in the recipe
    const twin = editable.some((other) => other !== candidate && titleOf(other) === title);
    return [
      {
        key: candidate.step.step_id,
        title: twin ? MESSAGES.editors.steps.numbered(candidate.index + 1, title) : title,
        manual: edits?.some((saved) => saved.step_id === candidate.step.step_id) ?? false,
        detail:
          candidate.kind === 'rotation' && angle !== null
            ? MESSAGES.processing.thisPage.degrees(angle)
            : null,
        chosen: candidate.step.step_id === step?.step_id,
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
        size={editor.size(context)}
        onCommit={commit}
      />
    ),
  };
}
