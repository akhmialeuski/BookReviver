import { useCallback, useEffect, useMemo, useState } from 'react';
import type { PageStepChangeSchema, PageVersionSchema } from '@/api';
import { stepChain } from '@/features/editors/chain';
import { useClearHistory, usePageHistory, useUndo } from '@/features/processing/historyQueries';
import { lastStanding, stands } from '@/features/processing/pageHistory';
import { useChooseVersion, useRemakeVersion, useVersions } from '@/features/processing/queries';
import type { StepDraft } from '@/features/processing/recipe';
import { historyOf } from '@/features/processing/results';
import { fieldTitleOf, formSchemaOf } from '@/features/processing/schema';
import { ChangeRow, ResultRow } from '@/features/processing/TimelineRows';
import { TIMELINE_FILTERS, TimelineFilter, timelineOf } from '@/features/processing/timeline';
import type { Processing } from '@/features/processing/useProcessing';
import { useUndoKey } from '@/features/processing/useUndoKey';
import { HistoryFrame } from '@/features/workspace/HistoryFrame';
import { useActiveJobs } from '@/features/workspace/queries';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/ui/dialog';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The history of the open page in the panel of a stage, as one list: the changes of the open step and the results of the
 * step or of the stage, newest first, each row with a chip that says which it is.
 *
 * With a step open the list holds that step's changes and results. With none open it holds the results of the stage,
 * which are the versions no other version of the stage reads, the Changes filter is off and "Clear the history" is
 * gone, since the changes belong to a step. It shows three rows and "Show 3 more" adds three. The changes are read a
 * hundred at a time, and the next hundred only when the rows asked for reach beyond the ones loaded, since the results
 * are all read at once and a result older than the oldest change loaded could still have an unloaded change above it.
 *
 * A result is the current one when the page stands on it, which is when it is the current version of the stage or one that
 * version was made from. The versions of the whole stage tell that, so it holds for a page that a variant of the recipe
 * ran, whose steps are not the steps of the recipe on the screen.
 *
 * A change that stands has its own "Undo to here", which takes back that change and every change after it and asks first
 * when that is more than one; Ctrl+Z takes back the newest. A result that is not the current one may be made so when it
 * is a result of the stage, and carries the marks and the comment of the reader. "Clear the history" deletes for good
 * every event of the step on the page: its changes, its settings and hand edit, its results and the results of the later
 * steps that read them, so the list is empty. A section with nothing to show stays in its place, grey, and says why: no
 * page is chosen, the recipe is not saved so the step has no id, or nothing has happened on the page yet.
 */

const labels = MESSAGES.processing.timeline;

/** How many rows the list shows at first, and how many more "Show more" adds. */
const ROWS_AT_A_TIME = 3;

const NO_CHANGES: readonly PageStepChangeSchema[] = [];
const NO_VERSIONS: readonly PageVersionSchema[] = [];

/** What the dialog asks the reader to confirm. */
type Question = { kind: 'undo'; changeId: string; count: number } | { kind: 'clear' } | null;

/** The step that is open, which the timeline reads the changes and the results of. */
export type TimelineStep = Pick<StepDraft, 'stepId' | 'processorKey'>;

export function PageTimeline({
  processing,
  pageId,
  step,
  headId,
}: {
  processing: Processing;
  /** The open page, or undefined when none is chosen. */
  pageId: string | undefined;
  /** The step that is open, or null for the stage as a whole; its id is null while the recipe is not saved. */
  step: TimelineStep | null;
  /** The current version of the stage on the page, or undefined when it has none. */
  headId: string | undefined;
}): React.JSX.Element {
  const { projectId, stage, catalogue, recipe } = processing;
  const stepId = step?.stepId ?? null;
  const unsaved = step !== null && stepId === null;
  const processor = catalogue.find((entry) => entry.key === step?.processorKey);

  const history = usePageHistory(projectId, pageId, stage, stepId);
  // A step reads its own results from the server by its id, and the stage reads the versions no other version reads. A
  // step that is not saved has no id, and the list of the stage would show the results of every step
  const ofStep = useVersions(
    projectId,
    unsaved || stepId === null ? undefined : pageId,
    stage,
    stepId === null ? {} : { step: stepId },
  );
  // The versions of the whole stage tell what the page stands on, whichever recipe ran it: the step row of the server
  // follows the recipe the page was run by, which a variant that the rules chose is not the recipe of the open step
  const ofStage = useVersions(projectId, unsaved ? undefined : pageId, stage);
  const versions = stepId === null ? ofStage : ofStep;
  const undo = useUndo(projectId, stage);
  const clear = useClearHistory(projectId, stage);
  const choose = useChooseVersion(projectId, stage);
  const remake = useRemakeVersion(projectId);
  const activeJobs = useActiveJobs(projectId);
  const [filter, setFilter] = useState<TimelineFilter>(TimelineFilter.All);
  const [shown, setShown] = useState(ROWS_AT_A_TIME);
  const [question, setQuestion] = useState<Question>(null);

  const changes = history.data?.changes ?? NO_CHANGES;
  const changesTotal = history.data?.total ?? 0;
  // The page stands on the current version of the stage and every version that one was made from
  const standing = useMemo(() => {
    const ofWholeStage = ofStage.data ?? NO_VERSIONS;
    const head = ofWholeStage.find((version) => version.id === headId);
    return new Set(stepChain(ofWholeStage, head).map((version) => version.id));
  }, [ofStage.data, headId]);
  const entries = historyOf(versions.data ?? NO_VERSIONS, standing);
  // Only a result of the last step is a result of the stage, which is all that can be made the current one
  const canUse =
    step === null || recipe?.steps.findLast((entry) => entry.enabled)?.step_id === step.stepId;
  // The changes belong to a step, so a filter that asks for them while none is open shows everything
  const active = stepId === null && filter === TimelineFilter.Changes ? TimelineFilter.All : filter;
  const timeline = timelineOf(changes, changesTotal, entries, active);
  const rows = timeline.rows.slice(0, Math.min(shown, timeline.certain));
  const { more, certain } = timeline;
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = history;

  // The rows asked for may reach beyond the changes loaded, so the next hundred are read only then
  useEffect(() => {
    if (more && hasNextPage && !isFetchingNextPage && certain < shown) {
      void fetchNextPage();
    }
  }, [more, hasNextPage, isFetchingNextPage, certain, shown, fetchNextPage]);

  const known = !unsaved && versions.isSuccess && (stepId === null || history.isSuccess);
  const events = changesTotal + entries.length;
  const reason =
    pageId === undefined
      ? labels.noPage
      : unsaved
        ? labels.notSaved
        : known && events === 0
          ? labels.nothingYet
          : null;

  const schema = useMemo(
    () => (processor === undefined ? undefined : formSchemaOf(processor.parameters)),
    [processor],
  );
  const titleOf = useCallback(
    (name: string) => (schema === undefined ? name : fieldTitleOf(schema, name)),
    [schema],
  );
  // The result whose picture is being made again, from the moment it was asked for until its job has ended
  const remakingId =
    remake.isPending ||
    (remake.data !== undefined && activeJobs.data?.some((job) => job.id === remake.data.id))
      ? remake.variables?.path.version_id
      : undefined;

  // An undo of the newest change is never dropped while an earlier one settles: the requests of the book go to the server
  // one at a time in the order they were made, and each takes back the newest change that stands by then
  const takeBack = (changeId: string | null): void => {
    if (pageId !== undefined && stepId !== null) {
      undo.mutate({
        path: { project_id: projectId, page_id: pageId, stage, step_id: stepId },
        body: { change_id: changeId },
      });
    }
  };
  // When the loaded rows hold no change that stands, an older one that the next page holds may
  useUndoKey(() => {
    if (lastStanding(changes) !== undefined || hasNextPage) {
      takeBack(null);
    }
  });

  // The changes above a change are all loaded, so the ones that stand from the newest down to it are counted exactly
  const undoTo = (changeId: string): void => {
    const count = changes
      .slice(0, changes.findIndex((change) => change.id === changeId) + 1)
      .filter(stands).length;
    if (count > 1) {
      setQuestion({ kind: 'undo', changeId, count });
    } else {
      takeBack(changeId);
    }
  };
  const use = (version: PageVersionSchema): void => {
    if (pageId === undefined) {
      return;
    }
    if (version.files_removed) {
      remake.mutate({ path: { project_id: projectId, page_id: pageId, version_id: version.id } });
    } else {
      choose.mutate({
        path: { project_id: projectId, page_id: pageId, stage },
        body: { version_id: version.id },
      });
    }
  };
  const confirm = (): void => {
    if (question?.kind === 'undo') {
      takeBack(question.changeId);
    } else if (question?.kind === 'clear' && pageId !== undefined && stepId !== null) {
      clear.mutate({
        path: { project_id: projectId, page_id: pageId, stage, step_id: stepId },
      });
    }
    setQuestion(null);
  };
  const asked = question?.kind === 'undo' ? labels.confirmUndo : labels.confirmClear;
  // A refused read of the history has its own paragraph below, so it is not an error of an action as well
  const error = undo.error ?? clear.error ?? choose.error ?? remake.error;
  const remaining = Math.min(ROWS_AT_A_TIME, timeline.total - rows.length);

  return (
    <>
      <HistoryFrame
        count={known ? events : null}
        reason={reason}
        filters={
          <div className="grid min-w-0 gap-1">
            <fieldset
              className="m-0 flex min-w-0 flex-wrap gap-1.5 border-0 p-0"
              data-testid="page-history-filter"
            >
              <legend className="sr-only">{labels.filters.group}</legend>
              {TIMELINE_FILTERS.map((value) => (
                <Button
                  key={value}
                  variant={active === value ? 'secondary' : 'outline'}
                  size="sm"
                  aria-pressed={active === value}
                  disabled={value === TimelineFilter.Changes && stepId === null}
                  data-testid={`page-history-filter-${value}`}
                  onClick={() => {
                    setFilter(value);
                    setShown(ROWS_AT_A_TIME);
                  }}
                >
                  {labels.filters.labels[value]}
                </Button>
              ))}
            </fieldset>
            {stepId === null ? (
              <p
                className="text-xs break-words text-muted-foreground"
                data-testid="page-history-changes-hint"
              >
                {labels.changesNeedStep}
              </p>
            ) : null}
          </div>
        }
        notice={
          <>
            {history.isError ? <p className="text-xs text-destructive">{labels.failed}</p> : null}
            {error === null ? null : <ErrorAlert message={describeError(error)} />}
          </>
        }
      >
        <p className="text-xs break-words text-muted-foreground">
          {stepId === null ? labels.hint.stage : labels.hint.step}
        </p>
        {rows.length === 0 ? (
          known ? (
            <p className="text-sm text-muted-foreground" data-testid="page-history-empty">
              {labels.empty[active]}
            </p>
          ) : null
        ) : (
          <ol className="grid min-w-0 grid-cols-1 gap-2" data-testid="page-history-list">
            {rows.map((row) =>
              row.kind === 'change' ? (
                <ChangeRow
                  key={`change:${row.id}`}
                  change={row.change}
                  titleOf={titleOf}
                  disabled={undo.isPending}
                  onUndo={() => undoTo(row.id)}
                />
              ) : (
                <ResultRow
                  key={`result:${row.id}`}
                  projectId={projectId}
                  entry={row.entry}
                  processor={catalogue.find(
                    (candidate) => candidate.key === row.entry.version.processor.key,
                  )}
                  canUse={canUse}
                  disabled={choose.isPending || remakingId !== undefined}
                  using={
                    (choose.isPending && choose.variables?.body.version_id === row.id) ||
                    remakingId === row.id
                  }
                  onUse={() => use(row.entry.version)}
                />
              ),
            )}
          </ol>
        )}
        {remaining > 0 ? (
          <Button
            variant="outline"
            size="sm"
            className="w-fit"
            disabled={history.isFetchingNextPage}
            data-testid="page-history-more"
            onClick={() => setShown(shown + ROWS_AT_A_TIME)}
          >
            {labels.showMore(remaining)}
          </Button>
        ) : null}
        {stepId === null ? null : (
          <Button
            variant="ghost"
            size="sm"
            className="w-fit text-destructive hover:text-destructive"
            disabled={clear.isPending}
            data-testid="page-history-clear"
            onClick={() => setQuestion({ kind: 'clear' })}
          >
            {labels.confirmClear.open}
          </Button>
        )}
      </HistoryFrame>
      <Dialog open={question !== null} onOpenChange={(next) => !next && setQuestion(null)}>
        <DialogContent data-testid="page-history-dialog">
          {question === null ? null : (
            <>
              <DialogHeader>
                <DialogTitle>
                  {question.kind === 'undo'
                    ? labels.confirmUndo.title(question.count)
                    : labels.confirmClear.title}
                </DialogTitle>
                <DialogDescription>
                  {question.kind === 'undo'
                    ? labels.confirmUndo.body(question.count)
                    : labels.confirmClear.body}
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <Button variant="outline" onClick={() => setQuestion(null)}>
                  {labels.cancel}
                </Button>
                <Button
                  variant={question.kind === 'undo' ? 'default' : 'destructive'}
                  data-testid="page-history-confirm"
                  onClick={confirm}
                >
                  {asked.confirm}
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
