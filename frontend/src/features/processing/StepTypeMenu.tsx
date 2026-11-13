import { ArrowLeftRightIcon } from 'lucide-react';
import { typesAt } from '@/features/processing/order';
import { useReplaceStepType } from '@/features/processing/queries';
import type { StepDraft } from '@/features/processing/recipe';
import type { Processing } from '@/features/processing/useProcessing';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The menu on the card of a step that replaces its type: the processors of the stage the step may take where it stands,
 * its own type marked.
 *
 * The list is the catalogue of the stage, and in the usual order it leaves out a processor that would break a place it
 * must keep, which is what the server refuses, so the menu is the same for every stage and names no plugin. Choosing a
 * type replaces it at once in every recipe that holds the step, which keeps its place and its identifier, and the
 * screen follows from the events of the book. The settings the pages kept for the earlier type stay with it and come
 * back with it, and the history of the pages shows the replacement as one batch, which an undo takes back.
 *
 * A step has no type to replace until it is saved, and the menu waits while the draft holds changes, since the server
 * replaces the type in the saved recipe.
 */

const labels = MESSAGES.processing.steps.type;

export function StepTypeMenu({
  processing,
  step,
  title,
}: {
  processing: Processing;
  step: StepDraft;
  /** The title the step goes by. */
  title: string;
}): React.JSX.Element | null {
  const { bookId, stage, recipe, catalogue } = processing;
  const replace = useReplaceStepType(bookId);
  const stepId = step.stepId;
  if (recipe === undefined || stepId === null) {
    return null;
  }
  const choices = typesAt(processing.steps, catalogue, step.id, processing.orderMode);
  const waiting = processing.dirty || replace.isPending;

  return (
    <DropdownMenu onOpenChange={() => replace.reset()}>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={labels.open(title)}
          title={labels.open(title)}
          data-testid="step-type"
        >
          <ArrowLeftRightIcon />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-72" data-testid="step-type-list">
        <DropdownMenuLabel className="text-xs text-muted-foreground uppercase">
          {labels.title}
        </DropdownMenuLabel>
        <p className="px-2 pb-1 text-xs text-muted-foreground">
          {processing.dirty ? labels.saveFirst : labels.hint}
        </p>
        <DropdownMenuRadioGroup
          value={step.processorKey}
          onValueChange={(key) => {
            if (key === step.processorKey) {
              return;
            }
            replace.mutate({
              path: { book_id: bookId, stage, recipe_id: recipe.id, step_id: stepId },
              body: { processor_key: key, order: processing.orderMode },
            });
          }}
        >
          {choices.map((processor) => (
            <DropdownMenuRadioItem
              key={processor.key}
              value={processor.key}
              disabled={waiting}
              data-testid="step-type-option"
              data-processor={processor.key}
              data-current={processor.key === step.processorKey}
            >
              <span className="min-w-0 flex-1 truncate">{processor.title}</span>
              {processor.key === step.processorKey ? (
                <span className="text-xs text-muted-foreground">{labels.current}</span>
              ) : null}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
        {replace.isError ? (
          <div className="p-2" data-testid="step-type-error">
            <ErrorAlert message={`${labels.failed} ${describeError(replace.error)}`} />
          </div>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
