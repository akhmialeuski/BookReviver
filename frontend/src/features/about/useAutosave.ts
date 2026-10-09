import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { type ProjectSchema, updateProjectApiV1ProjectsProjectIdPatch } from '@/api';
import {
  projectApiV1ProjectsProjectIdGetQueryKey,
  updateProjectApiV1ProjectsProjectIdPatchMutation,
} from '@/api/@tanstack/react-query.gen';
import { type SaveState, saveState } from '@/features/about/autosave';
import {
  type EditableFields,
  hasChanges,
  type Problems,
  planSave,
  toFields,
} from '@/features/about/fields';
import { invalidateProject, invalidateProjectList } from '@/features/projects/queries';
import { useDebouncedCallback } from '@/shared/hooks/useDebouncedCallback';
import { describeError } from '@/shared/http/problem';

/**
 * The form state of the About tab and its saving.
 *
 * The form shows the fields of the book with the person's edits over them, and sends the difference after the
 * person stops typing, or at once for a choice made with a click. One request is in flight at a time, and what was
 * typed meanwhile goes out after it. A refusal is not sent again until something changes or the person asks, and a
 * change still waiting when the tab is left is sent as the tab closes.
 */

/** How long after the last key the typed changes are sent. */
const DEBOUNCE_MS = 700;

/** How a change reaches the server. */
export interface EditOptions {
  /** Send it without waiting for more typing, as for a choice made with a click. */
  readonly immediate?: boolean;
}

/** What the form reads and calls. */
export interface Autosave {
  /** The fields as the form shows them: the saved ones with the edits over them. */
  readonly fields: EditableFields;
  /** The fields that hold a value that is not sent. */
  readonly problems: Problems;
  readonly state: SaveState;
  /** When the last save finished, or null before the first. */
  readonly savedAt: Date | null;
  /** Why the server refused the changes, while the state is `failed`. */
  readonly error: string;
  /** Change fields of the form. */
  edit(changes: Partial<EditableFields>, options?: EditOptions): void;
  /** Send the refused changes again. */
  retry(): void;
}

/**
 * Keep the form state of a book and save it as it changes.
 *
 * @param project The book as the server holds it.
 * @returns The fields to show, the state of the saving and the calls that change them.
 */
export function useAutosave(project: ProjectSchema): Autosave {
  const queryClient = useQueryClient();
  const saved = useMemo(() => toFields(project), [project]);
  const [edits, setEdits] = useState<Partial<EditableFields>>({});
  const [immediate, setImmediate] = useState(false);
  // The patches are told apart by their text, so a patch the server has taken is not sent again when the value it
  // stored differs from the one typed, as a trimmed name does
  const [settledKey, setSettledKey] = useState('');
  const [failedKey, setFailedKey] = useState('');
  const [savedAt, setSavedAt] = useState<Date | null>(null);

  const { mutate, isPending, error } = useMutation({
    ...updateProjectApiV1ProjectsProjectIdPatchMutation(),
    onSuccess: (updated, variables) => {
      setSettledKey(JSON.stringify(variables.body));
      setFailedKey('');
      setSavedAt(new Date());
      queryClient.setQueryData(
        projectApiV1ProjectsProjectIdGetQueryKey({ path: { project_id: updated.id } }),
        updated,
      );
      void invalidateProjectList(queryClient);
    },
    onError: (_error, variables) => setFailedKey(JSON.stringify(variables.body)),
  });

  const plan = useMemo(() => planSave(saved, edits), [saved, edits]);
  const dirty = hasChanges(plan) && plan.key !== settledKey;
  const failed = dirty && plan.key === failedKey;
  const projectId = project.id;

  // The send of the tab is dropped at the unmount, because the effect below sends the last changes directly then
  const send = useDebouncedCallback(
    (patch: typeof plan.patch) => mutate({ path: { project_id: projectId }, body: patch }),
    DEBOUNCE_MS,
    { flushOnUnmount: false },
  );
  useEffect(() => {
    if (!dirty || failed || isPending) {
      send.cancel();
      return;
    }
    send(plan.patch);
    if (immediate) {
      send.flush();
    }
  }, [dirty, failed, isPending, immediate, plan.patch, send]);

  // The unmount of the tab cannot use the mutation, whose observer goes with it, so the last changes go directly
  const pending = useRef({ send: false, patch: plan.patch });
  useEffect(() => {
    pending.current = { send: dirty && !failed && !isPending, patch: plan.patch };
  });
  useEffect(
    () => () => {
      if (pending.current.send) {
        void updateProjectApiV1ProjectsProjectIdPatch({
          path: { project_id: projectId },
          body: pending.current.patch,
        }).then(() => invalidateProject(queryClient, projectId));
      }
    },
    [queryClient, projectId],
  );

  const edit = useCallback((changes: Partial<EditableFields>, options?: EditOptions) => {
    setEdits((previous) => ({ ...previous, ...changes }));
    setImmediate(options?.immediate ?? false);
  }, []);
  const retry = useCallback(() => setFailedKey(''), []);

  const fields = useMemo(() => ({ ...saved, ...edits }), [saved, edits]);

  return {
    fields,
    problems: plan.problems,
    state: saveState({
      dirty,
      failed,
      saving: isPending,
      invalid: Object.keys(plan.problems).length > 0,
      saved: savedAt !== null,
    }),
    savedAt,
    error: failed ? describeError(error) : '',
    edit,
    retry,
  };
}
