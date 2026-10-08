import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { FigureState, ScanSchema } from '@/api';
import { getVersionApiV1ProjectsProjectIdPagesPageIdVersionsVersionIdGetOptions } from '@/api/@tanstack/react-query.gen';
import { stepChain, stepVersion } from '@/features/editors/chain';
import { figureStateOf } from '@/features/editors/figure';
import { pictureOf } from '@/features/editors/picture';
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
import { type StepSettings, StepSettingsContext } from '@/features/editors/stepSettings';
import type { PageContext } from '@/features/editors/types';
import { useHeld } from '@/features/editors/useHeld';
import { usePictureSize } from '@/features/editors/usePictureSize';
import { type ImageSource, sourceOfPreview } from '@/features/processing/compare';
import { invalidateHistory, useUndo } from '@/features/processing/historyQueries';
import { settingsOf } from '@/features/processing/pageSettings';
import type { PreviewRequest } from '@/features/processing/preview';
import {
  usePageSettings,
  useRunInFlight,
  useRunStage,
  useSetValue,
  useVersions,
} from '@/features/processing/queries';
import { bodyOf, draftOf } from '@/features/processing/recipe';
import { readResult } from '@/features/processing/results';
import { usePreview } from '@/features/processing/usePreview';
import type { Processing } from '@/features/processing/useProcessing';
import {
  invalidateStageRows,
  invalidateStageSummary,
  invalidateVersions,
} from '@/features/projects/queries';
import { isTypingTarget } from '@/features/viewer/keys';
import { useActiveJobs } from '@/features/workspace/queries';
import type { StripItem } from '@/features/workspace/strip';
import { useDebouncedCallback } from '@/shared/hooks/useDebouncedCallback';
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
 * "Auto" deletes the edit and runs again, and Ctrl+Z asks the server to take back the newest change of the step on the
 * page, which the history of the page keeps: the edit it had before is put back, or deleted when it had none, and a
 * setting of the page changed after the edit is taken back first. The stage is run again when an edit was put back.
 *
 * A shape moved in small steps, such as by the arrow keys, is saved once the steps pause. That save and the one made when
 * the reader lets go of a handle, types an angle or presses a button are two ways to write one shape, so the order between
 * them is decided here and nowhere else: a save made now drops the save of the same step on the same page that still waits,
 * since the waiting one holds an older shape and would write over the newer one.
 */

/**
 * The shape being moved, which stands in for the saved one until the save that was made from it has settled.
 *
 * The saved edit changes whenever any save of the page settles, and the saves go to the server one after another, so a
 * draft cannot tell by the saved edit whether the server has its shape yet. It is dropped by the save it started, and a
 * save that settles after a newer shape was held leaves that shape alone.
 */
interface Draft {
  key: string;
  /** Which hold of a shape made it, counted up for every shape the reader held. */
  seq: number;
  geometry: Geometry;
}

/** The size an editor that paints a mask is given when the step has not said how large its picture is. */
const EMPTY_SIZE = { width: 0, height: 0 };

/** Quiet time after the last small step of a shape before it is saved. */
const NUDGE_SAVE_DELAY_MS = 600;

/**
 * A save that waits for the reader to pause. It carries the save of the page and step it was asked for, since the canvas
 * stays mounted when the reader turns the page, and a save read when the wait ends would go to the page then open.
 */
interface WaitingSave {
  /** The page and step of the save. */
  key: string;
  save: () => void;
}

/** A run that waits for the book to be free. */
interface WantedRun {
  pageId: string;
}

export function useEditorSession({
  processing,
  current,
  items,
  scans,
  before,
  focusStepId,
  serverFigure,
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
  /**
   * The state of the shape of the open step on the open page, as the server computed it for the rows of the step, or null
   * while that row is read. It decides the state shown whenever a step is open, so the rule lives in one place.
   */
  serverFigure?: FigureState | null;
}): EditorSession | null {
  const { projectId, stage, catalogue } = processing;
  const queryClient = useQueryClient();
  const { save, remove } = useEditChanges(projectId);
  const run = useRunStage(projectId, stage);
  const { mutate: takeBack } = useUndo(projectId, stage);
  const { mutate: startRun } = run;
  const activeJobs = useActiveJobs(projectId);
  const [wanted, setWanted] = useState<WantedRun | null>(null);
  const runInFlight = useRunInFlight(projectId);
  const idle = activeJobs.data !== undefined && activeJobs.data.length === 0 && !runInFlight;

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

  // Each step of the recipe made a version, and an editor starts from what its step found. The picture it lies on is the
  // one the server gives for the row of the page at the open step, which is what that step reads
  const head = current?.row?.version ?? null;
  const versions = useVersions(projectId, current?.page.id, stage);
  const listed = versions.data;
  const live = useMemo(() => stepChain(listed ?? [], head), [listed, head]);
  // The versions and the row of the page arrive apart. After a run the row can name a current version that the list does
  // not hold yet, and the chain is then cut short, so what the step found would be lost for a moment and the editor would
  // start from another shape. The chain of before is kept for that moment, and while a run on the page has taken its
  // current version away.
  const behind =
    head === null
      ? !idle || wanted !== null
      : listed !== undefined && !listed.some((version) => version.id === head.id);
  const chain = useHeld(`${current?.page.id}|${stage}`, live, behind);
  const foundMade =
    entry === undefined || recipe === undefined
      ? null
      : stepVersion(chain, recipe.steps, entry.index);
  // A page that did not meet the condition of the step passed it as it was, so the step found nothing on it
  const skipped = foundMade?.data.skipped_by_condition === true;
  const made = skipped ? null : (foundMade ?? (editable.length === 1 ? head : null));
  const focused = focusStepId !== undefined;
  // The settings the open page has for the step, which the editor of the content box reads and sets besides its shape
  const sets = kind === 'content-box';
  const pageSettings = usePageSettings(projectId, current?.page.id, stage, sets);
  const setting = useSetValue(projectId, stage);
  const pageSettingsOfStep = settingsOf(pageSettings.data, step?.step_id ?? null);
  // A page the step has not made a result on is looked at by a preview of the step, which finds what a run would find, so
  // the editor shows it as found without a run
  const wantsFound =
    sets &&
    focused &&
    made === null &&
    !skipped &&
    current !== undefined &&
    entry !== undefined &&
    recipe !== undefined;
  const previewRequest: PreviewRequest | null =
    wantsFound && recipe !== undefined && entry !== undefined && current !== undefined
      ? {
          pageId: current.page.id,
          // The server lays the values of the page and of the parts of the book it is in over the step of a preview
          steps: bodyOf(draftOf(recipe)),
          stepIndex: entry.index,
        }
      : null;
  const foundPreview = usePreview(projectId, stage, previewRequest, wantsFound);
  const foundVersion =
    wantsFound && foundPreview.shown?.page_id === current?.page.id ? foundPreview.shown : null;
  const foundInput = useQuery({
    ...getVersionApiV1ProjectsProjectIdPagesPageIdVersionsVersionIdGetOptions({
      path: {
        project_id: projectId,
        page_id: current?.page.id ?? '',
        version_id: foundVersion?.input_id ?? '',
      },
    }),
    enabled: foundVersion?.input_id != null,
  });
  // What the preview found is drawn on the picture the step read in the preview, so it waits for that picture
  const foundPicture = foundVersion === null ? null : sourceOfPreview(foundInput.data);
  const foundResult = foundVersion !== null && foundPicture !== null ? foundVersion : null;
  const result =
    made !== null ? readResult(made) : foundResult === null ? null : readResult(foundResult);
  const picture =
    editor === undefined || current === undefined || processor === undefined
      ? null
      : (foundPicture ?? pictureOf(editor.picture, scan, before, made));
  // A step open in the workspace shows its shape before it has run, so an editor that starts from the step's result
  // starts from the whole picture instead, and the picture is asked for its size
  const pictureSize = usePictureSize(
    picture,
    focused && editor?.needsResult === true && result === null,
  );
  const context: PageContext | undefined =
    current === undefined || processor === undefined
      ? undefined
      : {
          current,
          items,
          scan,
          result,
          processorKey: processor.key,
          pictureSize,
        };
  const owner = editor === undefined || context === undefined ? undefined : editor.owner(context);
  const available =
    processor !== undefined &&
    picture !== null &&
    !skipped &&
    (editor?.needsResult !== true || result !== null || (focused && pictureSize !== null));

  const edits = useEdits(projectId, owner?.id, stage, available);
  const saved = edits?.find((candidate) => candidate.step_id === step?.step_id);
  const savedGeometry: Geometry | null = saved?.geometry ?? null;

  const [opened, setOpened] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const heldShapes = useRef(0);
  const [error, setError] = useState<string | null>(null);

  const key = `${owner?.id}|${step?.step_id}`;
  // One save waits at a time. The delayed call makes it, and a shape still waiting when the editor goes away is saved at
  // once, so a nudge is never lost
  const waiting = useRef<WaitingSave | null>(null);
  const saveWaiting = useDebouncedCallback(
    (waited: WaitingSave) => {
      waiting.current = null;
      waited.save();
    },
    NUDGE_SAVE_DELAY_MS,
    { flushOnUnmount: true },
  );
  const openKey = `${current?.page.id}|${stage}`;
  // The shape of an open step is on the page whether or not the reader has pressed "Set by hand"
  const alwaysOn = editor?.alwaysOn === true || focused;
  const active = editor !== undefined && picture !== null && (alwaysOn || opened === openKey);

  // The run waits for the book to be free, and is asked for once whatever the number of saves before it
  useEffect(() => {
    if (wanted === null || !idle) {
      return;
    }
    setWanted(null);
    startRun(
      {
        path: { project_id: projectId, stage },
        body: { page_ids: [wanted.pageId] },
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

  /**
   * Save the edit of the open step, or delete it for null, and read it again.
   *
   * @param next The shape to save, or null to delete the edit.
   * @param seq The hold of the draft that stands in for the shape until the save has settled.
   */
  const write = async (next: Geometry | null, seq: number): Promise<void> => {
    if (
      editor === undefined ||
      context === undefined ||
      owner === undefined ||
      processor === undefined ||
      step === undefined
    ) {
      return;
    }
    // This is the newest shape of the step on the page, and a save that waits for a pause holds an older one
    if (waiting.current?.key === key) {
      saveWaiting.cancel();
      waiting.current = null;
    }
    const path = {
      project_id: projectId,
      page_id: owner.id,
      stage,
      step_id: step.step_id,
    };
    let taken = false;
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
      taken = true;
      setError(null);
      if (editor.runsAfterEdit(context) && recipe !== undefined) {
        setWanted({ pageId: owner.id });
      }
    } catch (failure) {
      setError(describeError(failure));
    } finally {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: editsKey(projectId, owner.id, stage) }),
        // A saved or deleted edit is a change of the history of the step, and the results of the page are read again
        // with it, as after every other change of a version
        invalidateHistory(queryClient),
        invalidateVersions(queryClient, projectId, owner.id),
        invalidateStageRows(queryClient, projectId, stage),
        invalidateStageSummary(queryClient, projectId),
      ]);
      // The server has the shape and the edit is read again, so the draft is no longer needed. A shape held meanwhile is
      // another draft, and a save that did not go through keeps its draft on screen beside the error
      if (taken) {
        setDraft((held) => (held?.seq === seq ? null : held));
      }
    }
  };

  // A setting of the open page for the step is saved at once, and the stage is run on the page after it, as after an edit
  const setSetting = (name: string, value: unknown): void => {
    if (owner === undefined || step === undefined || recipe === undefined) {
      return;
    }
    setting.mutate(
      {
        path: { project_id: projectId, stage, step_id: step.step_id, name },
        body: { scope: 'pages', page_ids: [owner.id], value },
      },
      {
        onSuccess: () => {
          setError(null);
          setWanted({ pageId: owner.id });
        },
        onError: (failure) => setError(describeError(failure)),
      },
    );
  };

  const undo = (): void => {
    if (
      owner === undefined ||
      step === undefined ||
      editor === undefined ||
      context === undefined
    ) {
      return;
    }
    setDraft(null);
    takeBack(
      {
        path: { project_id: projectId, page_id: owner.id, stage, step_id: step.step_id },
        body: { change_id: null },
      },
      {
        onSuccess: (undone) => {
          if (
            recipe !== undefined &&
            editor.runsAfterEdit(context) &&
            undone.changes.some((change) => change.layer === 'hand')
          ) {
            setWanted({ pageId: owner.id });
          }
        },
        onError: (failure) => setError(describeError(failure)),
      },
    );
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
    draft !== null && draft.key === key ? draft.geometry : (savedGeometry ?? fallback);
  // Inside the workspace the state is the one the server computed for the row of the step. Outside it there is no such row,
  // so the same rule is applied to what the screen has, which also covers the moment before the row arrives. A saved edit
  // is shown as set by hand at once, since the row is read again only after the save and would still say "found" until then
  const figure =
    savedGeometry !== null
      ? 'by-hand'
      : foundResult !== null
        ? 'found'
        : focused &&
            serverFigure !== undefined &&
            serverFigure !== null &&
            serverFigure !== 'skipped'
          ? serverFigure
          : figureStateOf(false, made !== null);
  const reachRect = editor.reach(context);
  const hold = (next: Geometry): number => {
    heldShapes.current += 1;
    setDraft({ key, seq: heldShapes.current, geometry: next });
    return heldShapes.current;
  };
  const commit = (next: Geometry): void => {
    void write(next, hold(next));
  };
  const commitLater = (next: Geometry): void => {
    // The delay holds one save, so a shape waiting for another page or step is saved now rather than replaced by this one
    if (waiting.current !== null && waiting.current.key !== key) {
      saveWaiting.flush();
    }
    waiting.current = { key, save: () => commit(next) };
    saveWaiting(waiting.current);
  };
  const saving = save.isPending || remove.isPending;
  const stepSettings: StepSettings = {
    values: pageSettingsOfStep?.effective ?? step?.params ?? {},
    set: setSetting,
    busy: saving || setting.isPending || wanted !== null || run.isPending,
  };
  const titleOf = (candidate: EditableStep): string => MESSAGES.editors.steps.kinds[candidate.kind];
  const steps = editable.flatMap((candidate): StepChoice[] => {
    if (recipe === undefined) {
      return [];
    }
    const candidateMade = stepVersion(chain, recipe.steps, candidate.index);
    const angle =
      candidateMade === null || candidateMade.data.skipped_by_condition === true
        ? null
        : readResult(candidateMade).angle;
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
    alwaysOn,
    focused,
    figure,
    active,
    steps,
    choose: (stepKey: string) => {
      setChosen(stepKey);
      setDraft(null);
      setOpened(openKey);
    },
    hasEdit: savedGeometry !== null,
    busy: saving || setting.isPending || wanted !== null || run.isPending,
    error,
    open: () => setOpened(openKey),
    close: () => setOpened(null),
    auto: () => {
      if (savedGeometry !== null) {
        void write(null, hold(fallback));
      }
    },
    reach: reachRect === null ? null : { rect: reachRect, size: editor.size(context) },
    renderCanvas: (scene: EditorScene) => (
      <StepSettingsContext.Provider value={stepSettings}>
        <editor.Canvas
          scene={scene}
          geometry={geometry}
          size={editor.size(context)}
          context={context}
          figure={figure}
          onChange={hold}
          onCommit={commit}
          onCommitLater={commitLater}
        />
      </StepSettingsContext.Provider>
    ),
    renderPanel: () => (
      <StepSettingsContext.Provider value={stepSettings}>
        <editor.Panel
          geometry={geometry}
          processorKey={processor.key}
          params={step?.params ?? {}}
          disabled={saving}
          size={editor.size(context)}
          onChange={hold}
          onCommit={commit}
        />
      </StepSettingsContext.Provider>
    ),
  };
}
