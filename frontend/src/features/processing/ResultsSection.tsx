import { useState } from 'react';
import type { ResultMark } from '@/api';
import { factsOf } from '@/features/processing/facts';
import { useChooseVersion, useRemakeVersion, useVersions } from '@/features/processing/queries';
import { ResultNote } from '@/features/processing/ResultNote';
import { describeParams, historyOf, readResult } from '@/features/processing/results';
import type { Processing } from '@/features/processing/useProcessing';
import { useActiveJobs } from '@/features/workspace/queries';
import { describeError } from '@/shared/http/problem';
import { formatDateTime } from '@/shared/lib/format';
import { MESSAGES } from '@/shared/messages';
import { Badge } from '@/shared/ui/badge';
import { Button } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The results a stage or one of its steps made on the open page, newest first, with the notes of the reader on each.
 *
 * One section serves every step of every stage and the stage as a whole, so it knows nothing of a stage. Given a step it
 * lists the results of that step, which the server tells by the step; given none it lists the results of the stage, which
 * are the versions no other version of the stage reads. Each result says when and how it was made, with what settings and
 * what it found, whether it is the current one, and whether its picture was removed. It offers the mark and the comment,
 * and the way to make an earlier result the current one. The reader may narrow the list to the results marked good or bad.
 */

const labels = MESSAGES.processing.history;
const FILTERS: readonly (ResultMark | null)[] = [null, 'good', 'bad'];

export function ResultsSection({
  processing,
  pageId,
  stepId = null,
  currentId,
  canUse = true,
}: {
  processing: Processing;
  /** The open page. */
  pageId: string;
  /** The step whose results are listed, or null for the results of the stage as a whole. */
  stepId?: string | null;
  /** The result the page stands on now, whether of the step or of the stage, or undefined when it has none. */
  currentId: string | undefined;
  /** Whether an earlier result may be made the current one, which only a result of the stage may be. */
  canUse?: boolean;
}): React.JSX.Element {
  const { projectId, stage, catalogue } = processing;
  const [mark, setMark] = useState<ResultMark | null>(null);
  // The results of a stage are the versions no other version reads, which only the whole list tells, so a mark narrows
  // them here; the versions of one step are read from the server by the step and by the mark
  const ofStage = useVersions(projectId, stepId === null ? pageId : undefined, stage);
  const ofStep = useVersions(projectId, stepId === null ? undefined : pageId, stage, {
    step: stepId ?? undefined,
    mark: mark ?? undefined,
  });
  const found = stepId === null ? ofStage : ofStep;
  const entries = historyOf(found.data ?? [], currentId).filter(
    ({ version }) => stepId !== null || mark === null || version.mark === mark,
  );
  const choose = useChooseVersion(projectId, stage);
  const remake = useRemakeVersion(projectId);
  const activeJobs = useActiveJobs(projectId);
  // The result whose picture is being made again, from the moment it was asked for until its job has ended
  const remakingId =
    remake.isPending ||
    (remake.data !== undefined && activeJobs.data?.some((job) => job.id === remake.data.id))
      ? remake.variables?.path.version_id
      : undefined;

  return (
    <section
      className="grid gap-3"
      aria-label={labels.title}
      data-testid="results"
      data-scope={stepId === null ? 'stage' : 'step'}
    >
      <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
        {labels.title}
      </h3>
      <fieldset className="m-0 flex min-w-0 gap-1.5 border-0 p-0" data-testid="results-filter">
        <legend className="sr-only">{labels.filter.group}</legend>
        {FILTERS.map((value) => (
          <Button
            key={value ?? 'all'}
            variant={mark === value ? 'secondary' : 'outline'}
            size="sm"
            aria-pressed={mark === value}
            data-testid={`results-filter-${value ?? 'all'}`}
            onClick={() => setMark(value)}
          >
            {value === null ? labels.filter.all : labels.mark[value]}
          </Button>
        ))}
      </fieldset>
      {entries.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="results-empty">
          {mark === null ? labels.empty : labels.emptyMarked[mark]}
        </p>
      ) : (
        <ol className="grid gap-2" data-testid="history">
          {entries.map(({ version: entry, current }) => {
            const processor = catalogue.find((candidate) => candidate.key === entry.processor.key);
            const parts = describeParams(entry.params, processor?.parameters ?? {});
            const facts = factsOf(readResult(entry), entry.review !== null);
            return (
              <li
                key={entry.id}
                className="flex items-start gap-2 rounded-lg border px-3 py-2 text-sm"
                data-testid="history-entry"
                data-current={current}
                data-version={entry.id}
              >
                <div className="grid min-w-0 flex-1 gap-0.5">
                  <span className="text-muted-foreground">
                    {labels.made(formatDateTime(entry.created_at))}
                  </span>
                  <span className="text-muted-foreground" data-testid="history-origin">
                    {labels.origin[entry.origin]}
                  </span>
                  {parts.length === 0 ? null : (
                    <span className="break-words" data-testid="history-settings">
                      {parts.map((part) => `${part.label} ${part.value}`).join(' · ')}
                    </span>
                  )}
                  {facts.length === 0 ? null : (
                    <span className="break-words" data-testid="history-found">
                      {facts.map((fact) => `${fact.label} ${fact.value}`).join(' · ')}
                    </span>
                  )}
                  {entry.files_removed ? (
                    <span className="text-muted-foreground" data-testid="history-removed">
                      {labels.pictureRemoved}
                    </span>
                  ) : null}
                  <ResultNote projectId={projectId} version={entry} />
                </div>
                {current ? (
                  <Badge variant="secondary">{labels.current}</Badge>
                ) : canUse ? (
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={choose.isPending || remakingId !== undefined}
                    data-testid="history-use"
                    onClick={() =>
                      entry.files_removed
                        ? remake.mutate({
                            path: { project_id: projectId, page_id: pageId, version_id: entry.id },
                          })
                        : choose.mutate({
                            path: { project_id: projectId, page_id: pageId, stage },
                            body: { version_id: entry.id },
                          })
                    }
                  >
                    {(choose.isPending && choose.variables?.body.version_id === entry.id) ||
                    remakingId === entry.id
                      ? labels.using
                      : labels.use}
                  </Button>
                ) : null}
              </li>
            );
          })}
        </ol>
      )}
      {choose.error === null ? null : <ErrorAlert message={describeError(choose.error)} />}
      {remake.error === null ? null : <ErrorAlert message={describeError(remake.error)} />}
    </section>
  );
}
