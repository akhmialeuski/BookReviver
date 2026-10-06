import { ChevronDownIcon, ClockIcon } from 'lucide-react';
import { useMemo, useState } from 'react';
import type { PageStepChangeSchema, ProcessorSchema, Stage } from '@/api';
import { useHistoryOpen } from '@/features/processing/historyOpen';
import {
  HISTORY_PAGE_SIZE,
  useClearHistory,
  usePageHistory,
  useUndo,
} from '@/features/processing/historyQueries';
import { describeContent, lastStanding, stands } from '@/features/processing/pageHistory';
import { fieldTitleOf, formSchemaOf } from '@/features/processing/schema';
import { useUndoKey } from '@/features/processing/useUndoKey';
import { describeError } from '@/shared/http/problem';
import { formatDateTime } from '@/shared/lib/format';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';
import { Badge } from '@/shared/ui/badge';
import { Button } from '@/shared/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/shared/ui/collapsible';
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
 * The changes of one step on the open page, newest first, at the very end of the panel of the step.
 *
 * The section looks and behaves the same on every step of every stage, and only the text of its rows differs. It is
 * collapsed until the reader opens it, and whether it is open is remembered for every step. Collapsed, it shows its
 * title and how many changes there are. Open, it lists the newest three, "Show more" loads the next ones, and each row
 * that stands has its own "Undo to here", which takes back that change and every change after it and asks first when
 * that is more than one. "Clear the history" deletes the history of the step on the page and takes its settings and its
 * edit away, which cannot be undone. A step that is not saved has no history, since the server names a step by the
 * identifier it gives it when the recipe is saved, and a step with no change has nothing to show, so the section stays
 * in its place, grey, and says why. Ctrl+Z takes back the newest change that stands. An undo is written to the history
 * as a change of its own, so it is listed too and the rows it took back are marked. A key pressed twice quickly takes
 * back two changes, since the second undo waits for the first on the server. A change of a batch is taken back with its
 * whole batch, which the server does, so one press undoes it on every page it reached.
 */

const labels = MESSAGES.processing.steps.pageHistory;

const NO_CHANGES: readonly PageStepChangeSchema[] = [];

/** What the dialog asks the reader to confirm. */
type Question = { kind: 'undo'; changeId: string; count: number } | { kind: 'clear' } | null;

export function PageHistorySection({
  projectId,
  stage,
  stepId,
  pageId,
  processor,
}: {
  projectId: string;
  stage: Stage;
  /** The step of the recipe, or null while the recipe is not saved. */
  stepId: string | null;
  pageId: string;
  /** The processor of the step, which gives the fields in the rows their titles. */
  processor: ProcessorSchema | undefined;
}): React.JSX.Element {
  const history = usePageHistory(projectId, pageId, stage, stepId);
  const undo = useUndo(projectId, stage);
  const clear = useClearHistory(projectId, stage);
  const [open, setOpen] = useHistoryOpen();
  const [question, setQuestion] = useState<Question>(null);
  const changes = history.data?.changes ?? NO_CHANGES;
  const total = history.data?.total ?? 0;
  const schema = useMemo(
    () => (processor === undefined ? undefined : formSchemaOf(processor.parameters)),
    [processor],
  );
  const words = {
    titleOf: (name: string) => (schema === undefined ? name : fieldTitleOf(schema, name)),
    mask: labels.mask,
  };
  const reason =
    stepId === null ? labels.notSaved : history.isSuccess && total === 0 ? labels.nothingYet : null;
  const disabled = stepId === null || total === 0;
  const path = { project_id: projectId, page_id: pageId, stage, step_id: stepId ?? '' };

  // An undo of the newest change is never dropped while an earlier one settles: the requests of the book go to the server
  // one at a time in the order they were made, and each takes back the newest change that stands by then
  const takeBack = (changeId: string | null): void => {
    if (stepId !== null) {
      undo.mutate({ path, body: { change_id: changeId } });
    }
  };
  // When the loaded rows hold no change that stands, an older one that the next page holds may
  useUndoKey(() => {
    if (lastStanding(changes) !== undefined || history.hasNextPage) {
      takeBack(null);
    }
  });

  // The rows above a row are all loaded, so the standing ones from the newest down to it are counted exactly
  const undoTo = (index: number, changeId: string): void => {
    const count = changes.slice(0, index + 1).filter(stands).length;
    if (count > 1) {
      setQuestion({ kind: 'undo', changeId, count });
    } else {
      takeBack(changeId);
    }
  };
  const confirm = (): void => {
    if (question?.kind === 'undo') {
      takeBack(question.changeId);
    } else if (question?.kind === 'clear' && stepId !== null) {
      clear.mutate({ path });
    }
    setQuestion(null);
  };
  const asked = question?.kind === 'undo' ? labels.confirmUndo : labels.confirmClear;
  const error = undo.error ?? clear.error ?? history.error;

  return (
    <Collapsible
      open={open && !disabled}
      disabled={disabled}
      onOpenChange={setOpen}
      role="region"
      aria-label={labels.title}
      aria-disabled={disabled}
      className={cn('grid gap-2 rounded-md border bg-muted/30 p-3', disabled && 'opacity-60')}
      data-testid="page-history"
    >
      <CollapsibleTrigger
        className="group flex w-full items-center gap-2 text-left disabled:cursor-not-allowed"
        data-testid="page-history-toggle"
      >
        <ChevronDownIcon
          className="size-4 shrink-0 -rotate-90 text-muted-foreground transition-transform group-data-[state=open]:rotate-0"
          aria-hidden="true"
        />
        <ClockIcon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <h4 className="flex-1 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
          {labels.title}
        </h4>
        {disabled ? null : (
          <Badge variant="outline" data-testid="page-history-count">
            {labels.count(total)}
          </Badge>
        )}
      </CollapsibleTrigger>
      {reason === null ? null : (
        <p className="text-xs text-muted-foreground" data-testid="page-history-reason">
          {reason}
        </p>
      )}
      <CollapsibleContent className="grid gap-2">
        <p className="text-xs text-muted-foreground">{labels.hint}</p>
        <ol className="grid gap-2" data-testid="page-history-list">
          {changes.map((change, index) => {
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
                    aria-label={labels.undoHereLabel(labels.change(before, after))}
                    disabled={undo.isPending}
                    data-testid="page-history-undo-here"
                    onClick={() => undoTo(index, change.id)}
                  >
                    {labels.undoHere}
                  </Button>
                ) : null}
              </li>
            );
          })}
        </ol>
        {history.hasNextPage ? (
          <Button
            variant="outline"
            size="sm"
            className="w-fit"
            disabled={history.isFetchingNextPage}
            data-testid="page-history-more"
            onClick={() => void history.fetchNextPage()}
          >
            {labels.showMore(Math.min(HISTORY_PAGE_SIZE, total - changes.length))}
          </Button>
        ) : null}
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
      </CollapsibleContent>
      {history.isError ? <p className="text-xs text-destructive">{labels.failed}</p> : null}
      {error === null ? null : <ErrorAlert message={describeError(error)} />}
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
                  {asked.cancel}
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
    </Collapsible>
  );
}
