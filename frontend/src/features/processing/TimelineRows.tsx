import { Undo2Icon } from 'lucide-react';
import type { PageStepChangeSchema, ProcessorSchema } from '@/api';
import { factsOf } from '@/features/processing/facts';
import { describeContent, stands } from '@/features/processing/pageHistory';
import { ResultNote } from '@/features/processing/ResultNote';
import { describeParams, type HistoryEntry, readResult } from '@/features/processing/results';
import { formatDateTime } from '@/shared/lib/format';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';
import { Badge } from '@/shared/ui/badge';
import { Button } from '@/shared/ui/button';

/**
 * The two kinds of row of the timeline of a page: a change of the history of a step, and a result of a step or a stage.
 *
 * Both carry a small chip that names their kind. The text of a row wraps in a column of its own and the button of the
 * row keeps its size, so no row makes the panel scroll sideways.
 */

const labels = MESSAGES.processing.timeline;
const resultLabels = MESSAGES.processing.history;
const values = MESSAGES.processing.steps.values;

const ROW = 'min-w-0 rounded-lg border px-3 py-2 text-sm';
const META = 'flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground';

/** A change of a layer of the step on the page: who made it and when, what it did, and the way to take it back. */
export function ChangeRow({
  change,
  titleOf,
  disabled,
  onUndo,
}: {
  change: PageStepChangeSchema;
  /** Gives the title a field of the settings in the change is worded by. */
  titleOf: (name: string) => string;
  /** Whether an undo is on its way, which holds the buttons back. */
  disabled: boolean;
  /** Take back this change and every change after it. */
  onUndo: () => void;
}): React.JSX.Element {
  // A value of a part of the pages is worded by the part, since it is not a setting of this page alone
  const layer =
    change.scope === 'pages'
      ? labels.layers[change.layer]
      : change.scope === 'group'
        ? values.groupOf(change.group_label)
        : values.scopes[change.scope];
  const what = labels.what(layer, labels.sources[change.source]);
  const before = describeContent(change.layer, change.before, titleOf) ?? labels.nothing;
  const after = describeContent(change.layer, change.after, titleOf) ?? labels.nothing;
  return (
    <li
      className={cn(ROW, 'flex items-start gap-2')}
      data-testid="page-history-row"
      data-kind="change"
      data-change={change.id}
      data-undone={change.undone === true}
    >
      <div className="grid min-w-0 flex-1 gap-1">
        <div className={META}>
          <Badge variant="outline">{labels.chips.change}</Badge>
          <span className="min-w-0 break-words">
            {what} · {formatDateTime(change.created_at)}
          </span>
          {change.batch_id === null ? null : <span>{labels.batch}</span>}
          {change.undone === true ? <span>{labels.undone}</span> : null}
        </div>
        <p
          className={cn('min-w-0 break-words', change.undone === true && 'line-through')}
          data-testid="page-history-change"
        >
          {labels.change(before, after)}
        </p>
      </div>
      {stands(change) ? (
        <Button
          variant="outline"
          size="sm"
          className="shrink-0"
          aria-label={labels.undoHereLabel(labels.change(before, after))}
          disabled={disabled}
          data-testid="page-history-undo-here"
          onClick={onUndo}
        >
          <Undo2Icon />
          {labels.undoHere}
        </Button>
      ) : null}
    </li>
  );
}

/**
 * A result of the step or of the stage on the page: how and when it was made, with what settings and what it found,
 * whether it is the current one or may be made so, and the notes of the reader on it.
 */
export function ResultRow({
  projectId,
  entry,
  processor,
  canUse,
  disabled,
  using,
  onUse,
}: {
  projectId: string;
  entry: HistoryEntry;
  /** The processor that made the result, which gives the settings their titles. */
  processor: ProcessorSchema | undefined;
  /** Whether an earlier result may be made the current one, which only a result of the stage may be. */
  canUse: boolean;
  /** Whether a result is being made the current one, which holds the buttons back. */
  disabled: boolean;
  /** Whether this very result is being made the current one. */
  using: boolean;
  /** Make this result the current one, or make its picture again when the picture was removed. */
  onUse: () => void;
}): React.JSX.Element {
  const { version, current } = entry;
  const parts = describeParams(version.params, processor?.parameters ?? {});
  const facts = factsOf(readResult(version), version.review !== null);
  return (
    <li
      className={cn(ROW, 'grid gap-1.5')}
      data-testid="page-history-row"
      data-kind="result"
      data-current={current}
      data-version={version.id}
    >
      <div className="flex items-start gap-2">
        <div className="grid min-w-0 flex-1 gap-1">
          <div className={META}>
            <Badge variant="outline">{labels.chips.result}</Badge>
            <span className="min-w-0 break-words">
              <span data-testid="page-history-origin">{resultLabels.origin[version.origin]}</span> ·{' '}
              {formatDateTime(version.created_at)}
            </span>
          </div>
          {parts.length === 0 ? null : (
            <span className="min-w-0 break-words" data-testid="page-history-settings">
              {parts.map((part) => `${part.label} ${part.value}`).join(' · ')}
            </span>
          )}
          {facts.length === 0 ? null : (
            <span className="min-w-0 break-words" data-testid="page-history-found">
              {facts.map((fact) => `${fact.label} ${fact.value}`).join(' · ')}
            </span>
          )}
          {version.files_removed ? (
            <span className="text-muted-foreground" data-testid="page-history-removed">
              {resultLabels.pictureRemoved}
            </span>
          ) : null}
        </div>
        {current ? (
          <Badge variant="secondary">{resultLabels.current}</Badge>
        ) : canUse ? (
          <Button
            variant="outline"
            size="sm"
            className="shrink-0"
            disabled={disabled}
            data-testid="page-history-use"
            onClick={onUse}
          >
            {using ? resultLabels.using : resultLabels.use}
          </Button>
        ) : null}
      </div>
      <ResultNote projectId={projectId} version={version} />
    </li>
  );
}
