import { useEffect, useMemo, useState } from 'react';
import type { ProcessorSchema } from '@/api';
import { ParamsForm } from '@/features/processing/ParamsForm';
import { changedFields, effectiveParams, showValue } from '@/features/processing/pageSettings';
import { useResetPageSetting, useSetPageSetting } from '@/features/processing/queries';
import type { StepDraft } from '@/features/processing/recipe';
import { fieldTitleOf, formSchemaOf } from '@/features/processing/schema';
import type { Processing } from '@/features/processing/useProcessing';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The settings of one step that only the open page uses, under the settings of the step in the recipe.
 *
 * It lists the fields the page changes, each with the way back to the value of the recipe, and opens the form of the
 * step to change more. Changing a field in the form sets it for this page alone: the other pages keep the value of the
 * recipe, and this page keeps its value when the recipe changes. A step that is not saved yet has no settings of a page,
 * since the server names a step by the identifier it gives it when the recipe is saved.
 */

const labels = MESSAGES.processing.steps.pageSettings;

export function PageStepSettings({
  processing,
  step,
  processor,
  pageId,
  pageValues,
}: {
  processing: Pick<Processing, 'projectId' | 'stage'>;
  step: StepDraft;
  processor: ProcessorSchema | undefined;
  pageId: string;
  /** The fields the page changes for this step, by name. */
  pageValues: Readonly<Record<string, unknown>>;
}): React.JSX.Element {
  const { projectId, stage } = processing;
  const set = useSetPageSetting(projectId, stage);
  const reset = useResetPageSetting(projectId, stage);
  const [editing, setEditing] = useState(false);
  const effective = useMemo(
    () => effectiveParams(step.params, pageValues),
    [step.params, pageValues],
  );
  const [values, setValues] = useState(effective);
  const marked = useMemo(() => new Set(Object.keys(pageValues)), [pageValues]);
  const schema = useMemo(
    () => (processor === undefined ? undefined : formSchemaOf(processor.parameters)),
    [processor],
  );
  const stepId = step.stepId;
  const error = set.error ?? reset.error;

  // The form starts from what the page runs with each time it is opened, and not from what an earlier opening left
  useEffect(() => {
    if (editing) {
      setValues(effective);
    }
  }, [editing, effective]);

  if (stepId === null) {
    return (
      <p className="text-xs text-muted-foreground" data-testid="page-settings-save-first">
        {labels.saveFirst}
      </p>
    );
  }
  const names = Object.keys(pageValues);
  const path = { project_id: projectId, page_id: pageId, stage, step_id: stepId };

  return (
    <section
      className="grid gap-2 rounded-md border bg-muted/30 p-3"
      aria-label={labels.title}
      data-testid="page-settings"
    >
      <h4 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
        {labels.title}
      </h4>
      <p className="text-xs text-muted-foreground">{labels.hint}</p>
      {names.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="page-settings-none">
          {labels.none}
        </p>
      ) : (
        <ul className="grid gap-1" data-testid="page-settings-list">
          {names.map((name) => {
            const title = schema === undefined ? name : fieldTitleOf(schema, name);
            return (
              <li key={name} className="flex items-center justify-between gap-2 text-sm">
                <span>{labels.current(title, showValue(pageValues[name]))}</span>
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label={labels.takeBack(title)}
                  disabled={reset.isPending}
                  data-testid="page-settings-reset"
                  onClick={() => reset.mutate({ path: { ...path, name } })}
                >
                  {labels.takeBack(title)}
                </Button>
              </li>
            );
          })}
        </ul>
      )}
      {processor === undefined ? null : (
        <>
          <Button
            variant="outline"
            size="sm"
            className="w-fit"
            data-testid="page-settings-edit"
            onClick={() => setEditing(!editing)}
          >
            {editing ? labels.done : labels.change}
          </Button>
          {editing ? (
            <ParamsForm
              processor={processor}
              params={values}
              marked={marked}
              onChange={(next) => {
                for (const [name, value] of changedFields(values, next)) {
                  set.mutate({ path: { ...path, name }, body: { value } });
                }
                setValues(next);
              }}
            />
          ) : null}
        </>
      )}
      {error === null ? null : <ErrorAlert message={describeError(error)} />}
    </section>
  );
}
