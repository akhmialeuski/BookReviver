import { ChevronDownIcon, RotateCcwIcon } from 'lucide-react';
import { useState } from 'react';
import type { ResetBody, ResetImpactSchema, ResetScope, StepResetSchema } from '@/api';
import { useUndo } from '@/features/processing/historyQueries';
import { useResetImpact, useResetSteps } from '@/features/processing/queries';
import { ResetDialog } from '@/features/processing/ResetDialog';
import type { Processing } from '@/features/processing/useProcessing';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The menu that resets a step of the open stage to its defaults, on the open page or on every page, and what it did.
 *
 * A reset takes away the settings the page changed for the step and the hand edit the step reads, so the page uses the
 * recipe again and the next run finds the shape anew. The four choices are the step on the open page, every step of the
 * stage on the open page, the step on every page, and every step of the stage on every page. A reset that reaches other
 * pages counts the pages that lose work first and waits for the answer of the reader, unless it would take none. The
 * changes are one batch of the history, so the undo beside the line takes everything back at once.
 */

const labels = MESSAGES.processing.reset;

const SCOPES: readonly ResetScope[] = ['page-step', 'page', 'step', 'stage'];
const OF_THE_PAGE: ReadonlySet<ResetScope> = new Set(['page-step', 'page']);
const OF_THE_STEP: ReadonlySet<ResetScope> = new Set(['page-step', 'step']);
const OF_OTHER_PAGES: ReadonlySet<ResetScope> = new Set(['step', 'stage']);

export function StepReset({
  processing,
  pageId,
  stepId,
  title,
}: {
  processing: Pick<Processing, 'projectId' | 'stage'>;
  /** The open page, or undefined when the book has none, which leaves the choices of one page out. */
  pageId: string | undefined;
  stepId: string;
  /** What the step is called, which names the menu. */
  title: string;
}): React.JSX.Element {
  const { projectId, stage } = processing;
  const impact = useResetImpact();
  const reset = useResetSteps(projectId, stage);
  const undo = useUndo(projectId, stage);
  const [result, setResult] = useState<StepResetSchema | null>(null);
  const [asking, setAsking] = useState<{ impact: ResetImpactSchema; body: ResetBody } | null>(null);
  const first = result?.changes[0];
  const pages = new Set(result?.changes.map((change) => change.page_id)).size;

  const send = (body: ResetBody): void => {
    reset.mutate({ path: { project_id: projectId, stage }, body }, { onSuccess: setResult });
  };

  const start = (scope: ResetScope): void => {
    const body: ResetBody = {
      scope,
      ...(OF_THE_PAGE.has(scope) && pageId !== undefined ? { page_id: pageId } : {}),
      ...(OF_THE_STEP.has(scope) ? { step_id: stepId } : {}),
    };
    setResult(null);
    if (!OF_OTHER_PAGES.has(scope)) {
      send(body);
      return;
    }
    // A reset that reaches other pages asks first, with the number of pages it takes work from, unless it takes none
    impact.mutate(
      { path: { project_id: projectId, stage }, body },
      {
        onSuccess: (counted) =>
          counted.affected === 0 ? send(body) : setAsking({ impact: counted, body }),
      },
    );
  };

  return (
    <div className="grid gap-1">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="outline"
            size="sm"
            className="w-fit"
            title={labels.hint}
            aria-label={labels.ofStep(title)}
            disabled={reset.isPending || impact.isPending}
            data-testid="reset-menu"
          >
            <RotateCcwIcon />
            {labels.label}
            <ChevronDownIcon />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          {SCOPES.map((scope) => (
            <DropdownMenuItem
              key={scope}
              disabled={OF_THE_PAGE.has(scope) && pageId === undefined}
              data-testid={`reset-${scope}`}
              onSelect={() => start(scope)}
            >
              {labels.scopes[scope]}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      {result === null ? null : (
        <p
          className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground"
          data-testid="reset-result"
        >
          <span>{labels.done(result.changes.length, pages)}</span>
          {first === undefined ? null : (
            <Button
              variant="outline"
              size="sm"
              disabled={undo.isPending}
              data-testid="reset-undo"
              onClick={() =>
                undo.mutate(
                  {
                    path: {
                      project_id: projectId,
                      page_id: first.page_id,
                      stage,
                      step_id: first.step_id,
                    },
                    body: { change_id: first.id },
                  },
                  { onSuccess: () => setResult(null) },
                )
              }
            >
              {labels.undo}
            </Button>
          )}
        </p>
      )}
      {impact.error === null && reset.error === null && undo.error === null ? null : (
        <ErrorAlert message={describeError(impact.error ?? reset.error ?? undo.error)} />
      )}
      <ResetDialog
        impact={asking?.impact ?? null}
        onConfirm={() => {
          if (asking !== null) {
            send({ ...asking.body, confirm: true });
          }
          setAsking(null);
        }}
        onCancel={() => setAsking(null)}
      />
    </div>
  );
}
