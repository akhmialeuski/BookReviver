import { useMemo } from 'react';
import type { PageStepChangeSchema, ProcessorSchema } from '@/api';
import { usePageHistory, useUndo } from '@/features/processing/historyQueries';
import { describeContent, lastStanding, stands } from '@/features/processing/pageHistory';
import type { StepDraft } from '@/features/processing/recipe';
import { fieldTitleOf, formSchemaOf } from '@/features/processing/schema';
import type { Processing } from '@/features/processing/useProcessing';
import { useUndoKey } from '@/features/processing/useUndoKey';
import { describeError } from '@/shared/http/problem';
import { formatDateTime } from '@/shared/lib/format';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The changes of one step on the open page, newest first, with the way back to any of them.
 *
 * Each row says what changed, who changed it, when, and what the layer held before and after. "Undo" and Ctrl+Z take
 * back the newest change that stands, and "Undo back to here" takes back a row and every change after it. An undo is
 * written to the history as a change of its own, so it is listed too and the rows it took back are marked. A key pressed
 * twice quickly takes back two changes, since the second undo waits for the first on the server. A change of
 * a batch is taken back with its whole batch, which the server does, so one press undoes it on every page it reached.
 * A step that is not saved yet has no history, since the server names a step by the identifier it gives it when the
 * recipe is saved.
 */

const labels = MESSAGES.processing.steps.pageHistory;

const NO_CHANGES: readonly PageStepChangeSchema[] = [];

export function PageStepHistory({
  processing,
  step,
  processor,
  pageId,
}: {
  processing: Pick<Processing, 'projectId' | 'stage'>;
  step: StepDraft;
  processor: ProcessorSchema | undefined;
  pageId: string;
}): React.JSX.Element | null {
  const { projectId, stage } = processing;
  const stepId = step.stepId;
  const history = usePageHistory(projectId, pageId, stage, stepId);
  const undo = useUndo(projectId, stage);
  const changes = history.data ?? NO_CHANGES;
  const last = lastStanding(changes);
  const schema = useMemo(
    () => (processor === undefined ? undefined : formSchemaOf(processor.parameters)),
    [processor],
  );

  // An undo of the newest change is never dropped while an earlier one settles: the requests of the book go to the server
  // one at a time in the order they were made, and each takes back the newest change that stands by then
  const takeBack = (changeId: string | null): void => {
    if (stepId !== null) {
      undo.mutate({
        path: { project_id: projectId, page_id: pageId, stage, step_id: stepId },
        body: { change_id: changeId },
      });
    }
  };
  useUndoKey(() => {
    if (last !== undefined) {
      takeBack(null);
    }
  });

  if (stepId === null) {
    return null;
  }
  const words = {
    titleOf: (name: string) => (schema === undefined ? name : fieldTitleOf(schema, name)),
    mask: labels.mask,
  };
  const error = undo.error ?? history.error;

  return (
    <section
      className="grid gap-2 rounded-md border bg-muted/30 p-3"
      aria-label={labels.title}
      data-testid="page-history"
    >
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
          {labels.title}
        </h4>
        <Button
          variant="outline"
          size="sm"
          title={labels.undoLast}
          aria-label={labels.undoLast}
          disabled={last === undefined}
          data-testid="page-history-undo"
          onClick={() => takeBack(null)}
        >
          {labels.undo}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">{labels.hint}</p>
      {changes.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="page-history-none">
          {labels.none}
        </p>
      ) : (
        <ol className="grid max-h-64 gap-2 overflow-y-auto" data-testid="page-history-list">
          {changes.map((change) => {
            const what = labels.what(labels.layers[change.layer], labels.sources[change.source]);
            const before = describeContent(change.layer, change.before, words) ?? labels.nothing;
            const after = describeContent(change.layer, change.after, words) ?? labels.nothing;
            return (
              <li
                key={change.id}
                className="grid gap-1 border-b pb-2 text-sm last:border-b-0 last:pb-0"
                data-testid="page-history-row"
                data-undone={change.undone === true}
              >
                <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                  <span>
                    {what} · {formatDateTime(change.created_at)}
                  </span>
                  <span className="flex gap-2">
                    {change.batch_id === null ? null : <span>{labels.batch}</span>}
                    {change.undone === true ? <span>{labels.undone}</span> : null}
                  </span>
                </div>
                <p className={change.undone === true ? 'line-through' : undefined}>
                  {labels.change(before, after)}
                </p>
                {stands(change) ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="w-fit"
                    aria-label={labels.undoBackLabel(labels.change(before, after))}
                    disabled={undo.isPending}
                    data-testid="page-history-undo-back"
                    onClick={() => takeBack(change.id)}
                  >
                    {labels.undoBack}
                  </Button>
                ) : null}
              </li>
            );
          })}
        </ol>
      )}
      {history.isError ? <p className="text-xs text-destructive">{labels.failed}</p> : null}
      {error === null ? null : <ErrorAlert message={describeError(error)} />}
    </section>
  );
}
