import { useState } from 'react';
import type { CarryOverSchema, CarryScope } from '@/api';
import { useUndo } from '@/features/processing/historyQueries';
import { useCarryOver } from '@/features/processing/queries';
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
 * The menu that carries the value the open page has for one field of a step over to other pages, and what it did.
 *
 * The value goes to the pages after the open one, to the pages selected in the grid, or to every page the step
 * processes. A page that has a value of its own for the field is skipped, and the line under the menu says how many
 * were, unless the reader asked to write over them. The pages the value reached are one batch of the history, so the
 * undo beside the line takes it back from all of them at once. The server decides which pages the value reaches, so the
 * menu names no count for the first and the last choice.
 */

const labels = MESSAGES.processing.steps.pageSettings.carry;

export function CarryOver({
  processing,
  pageId,
  stepId,
  name,
  title,
  selected,
  overwrite,
  children,
}: {
  processing: Pick<Processing, 'projectId' | 'stage'>;
  pageId: string;
  stepId: string;
  /** The field in the parameters of the step. */
  name: string;
  /** What the field is called in the form. */
  title: string;
  /** The pages selected in the grid, which the open page may be one of. */
  selected: ReadonlySet<string>;
  /** Whether a page that has another value of its own takes the value as well. */
  overwrite: boolean;
  /** What stands beside the menu, such as the way back to the value of the recipe. */
  children?: React.ReactNode;
}): React.JSX.Element {
  const { projectId, stage } = processing;
  const carry = useCarryOver(projectId, stage);
  const undo = useUndo(projectId, stage);
  const [result, setResult] = useState<CarryOverSchema | null>(null);
  const others = [...selected].filter((id) => id !== pageId);
  const first = result?.changes[0];

  const send = (scope: CarryScope): void => {
    carry.mutate(
      {
        path: { project_id: projectId, page_id: pageId, stage, step_id: stepId, name },
        body: { scope, overwrite, ...(scope === 'selected' ? { page_ids: others } : {}) },
      },
      { onSuccess: setResult },
    );
  };

  return (
    <div className="grid justify-items-end gap-1">
      <div className="flex items-center gap-1">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              aria-label={labels.ofField(title)}
              disabled={carry.isPending}
              data-testid="carry-menu"
            >
              {labels.label}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem data-testid="carry-following" onSelect={() => send('following')}>
              {labels.following}
            </DropdownMenuItem>
            <DropdownMenuItem
              disabled={others.length === 0}
              data-testid="carry-selected"
              onSelect={() => send('selected')}
            >
              {labels.selected(others.length)}
            </DropdownMenuItem>
            <DropdownMenuItem data-testid="carry-condition" onSelect={() => send('condition')}>
              {labels.condition}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        {children}
      </div>
      {result === null ? null : (
        <p
          className="flex flex-wrap items-center justify-end gap-2 text-xs text-muted-foreground"
          data-testid="carry-result"
        >
          <span>{labels.done(result.changes.length, result.skipped.length)}</span>
          {first === undefined ? null : (
            <Button
              variant="outline"
              size="sm"
              disabled={undo.isPending}
              data-testid="carry-undo"
              onClick={() =>
                undo.mutate(
                  {
                    path: {
                      project_id: projectId,
                      page_id: first.page_id,
                      stage,
                      step_id: stepId,
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
      {carry.error === null && undo.error === null ? null : (
        <ErrorAlert message={describeError(carry.error ?? undo.error)} />
      )}
    </div>
  );
}
