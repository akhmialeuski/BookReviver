import { ChevronDownIcon, LoaderCircleIcon, PinIcon } from 'lucide-react';
import { useState } from 'react';
import type { StageRunBody } from '@/api';
import {
  useCreateRule,
  useRetargetRule,
  useRules,
  useRunInFlight,
  useRunStage,
  useUnpin,
} from '@/features/processing/queries';
import { pageIdsFor, RunScope } from '@/features/processing/scope';
import type { Processing } from '@/features/processing/useProcessing';
import {
  conditionOfKind,
  conditionOfPage,
  markOf,
  pagesLikePage,
  ruleFor,
  toneOf,
} from '@/features/processing/variants';
import { useActiveJobs } from '@/features/workspace/queries';
import type { StripItem } from '@/features/workspace/strip';
import { describeError } from '@/shared/http/problem';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The variant of the open page, and the way to give the variant shown in the panel to more pages.
 *
 * Applying to this page, to the selected pages or to all pages pins the variant, so a run of the stage on all pages
 * keeps it there. Applying to every page of the kind of this one makes a rule of the book instead, which also reaches
 * the pages of that kind that join the book later, and then runs the stage on the pages of that kind. The pin is taken
 * off with "Use the book's rules".
 */

const labels = MESSAGES.processing;

export function ApplyTo({
  processing,
  items,
  item,
  selected,
}: {
  processing: Processing;
  /** Every page of the book with where it stands in the stage. */
  items: readonly StripItem[];
  /** The open page. */
  item: StripItem;
  /** The pages selected in the grid. */
  selected: ReadonlySet<string>;
}): React.JSX.Element | null {
  const { projectId, stage, recipe, recipes } = processing;
  const run = useRunStage(projectId, stage);
  const unpin = useUnpin(projectId, stage);
  const rules = useRules(projectId, stage, recipe !== undefined);
  const create = useCreateRule(projectId, stage);
  const retarget = useRetargetRule(projectId, stage);
  const activeJobs = useActiveJobs(projectId);
  const runInFlight = useRunInFlight(projectId);
  const [failure, setFailure] = useState<unknown>(null);
  if (recipe === undefined) {
    return null;
  }

  const { page, row } = item;
  const mark = markOf(recipes, item);
  const used = recipes.find((entry) => entry.id === row?.recipe_id);
  const pinned = row?.pinned === true && used !== undefined;
  const busy =
    run.isPending ||
    create.isPending ||
    retarget.isPending ||
    (activeJobs.data?.length ?? 0) > 0 ||
    runInFlight;
  const blocked = processing.dirty || busy;
  const condition = conditionOfPage(page);
  const kindPages = pagesLikePage(items, page);
  // A page of another kind that shows a picture is named by that, since the rule on plates sends every picture
  const kindName =
    page.content_type === 'text' || conditionOfKind(page.kind) === 'plates'
      ? MESSAGES.pages.kinds[page.kind]
      : MESSAGES.processing.content.picture;

  const send = (body: StageRunBody): void => {
    setFailure(null);
    run.mutate({ path: { project_id: projectId, stage }, body });
  };

  const applyTo = (scope: RunScope): void => {
    const ids = pageIdsFor(scope, items, page.id, selected);
    send({ recipe_id: recipe.id, pin: true, ...(ids === null ? {} : { page_ids: ids }) });
  };

  const applyToKind = async (): Promise<void> => {
    if (condition === null) {
      return;
    }
    setFailure(null);
    try {
      const existing = ruleFor(rules.data ?? [], condition);
      if (existing === undefined) {
        await create.mutateAsync({
          path: { project_id: projectId, stage },
          body: { condition, recipe_id: recipe.id },
        });
      } else if (existing.recipe_id !== recipe.id) {
        await retarget.mutateAsync({
          path: { project_id: projectId, stage, rule_id: existing.id },
          body: { recipe_id: recipe.id },
        });
      }
      // The rules choose the variant of these pages now, and a page pinned to another variant keeps its own
      send({ page_ids: kindPages.map((entry) => entry.page.id) });
    } catch (error) {
      setFailure(error);
    }
  };

  const selectedCount = pageIdsFor(RunScope.Selected, items, page.id, selected)?.length ?? 0;
  const allCount = items.filter((entry) => entry.page.origin !== 'placeholder').length;
  const error = failure ?? run.error ?? unpin.error;

  return (
    <div className="grid gap-2" data-testid="apply-to">
      <div className="flex items-center justify-between gap-2 text-sm">
        <span className="text-muted-foreground">{labels.thisPage.variant}</span>
        <span className="flex items-center gap-1.5 font-medium" data-testid="page-variant">
          {mark === null ? (
            (used?.name ?? MESSAGES.workspace.strip.variant.none)
          ) : (
            <>
              <span className={cn('size-2 rounded-full', mark.tone)} aria-hidden="true" />
              {mark.name}
            </>
          )}
          {pinned ? <PinIcon className="size-3.5" aria-label={labels.thisPage.pinned} /> : null}
        </span>
      </div>
      <p className="text-xs text-muted-foreground" data-testid="page-variant-source">
        {pinned ? labels.thisPage.pinned : labels.thisPage.byRules}
      </p>
      <div className="flex flex-wrap gap-2">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="outline"
              size="sm"
              title={processing.dirty ? labels.apply.saveFirst : labels.apply.hint}
              disabled={blocked}
              data-testid="apply-menu"
            >
              {run.isPending ? <LoaderCircleIcon className="animate-spin" /> : null}
              <span
                className={cn('size-2 rounded-full', toneOf(recipes, recipe.id))}
                aria-hidden="true"
              />
              {labels.apply.menu(recipe.name)}
              <ChevronDownIcon />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start">
            <DropdownMenuLabel>{labels.apply.label}</DropdownMenuLabel>
            <DropdownMenuItem data-testid="apply-page" onSelect={() => applyTo(RunScope.Page)}>
              {labels.apply.page}
            </DropdownMenuItem>
            <DropdownMenuItem
              disabled={selectedCount === 0}
              data-testid="apply-selected"
              onSelect={() => applyTo(RunScope.Selected)}
            >
              {labels.apply.selected(selectedCount)}
            </DropdownMenuItem>
            <DropdownMenuItem
              disabled={condition === null || kindPages.length === 0}
              title={condition === null ? labels.apply.noRuleForKind(kindName) : undefined}
              data-testid="apply-kind"
              onSelect={() => void applyToKind()}
            >
              {labels.apply.kind(kindName, kindPages.length)}
            </DropdownMenuItem>
            <DropdownMenuItem data-testid="apply-all" onSelect={() => applyTo(RunScope.All)}>
              {labels.apply.all(allCount)}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        {pinned ? (
          <Button
            variant="ghost"
            size="sm"
            title={labels.thisPage.useRulesHint}
            disabled={unpin.isPending || busy}
            data-testid="use-rules"
            onClick={() =>
              unpin.mutate({ path: { project_id: projectId, page_id: page.id, stage } })
            }
          >
            {labels.thisPage.useRules}
          </Button>
        ) : null}
      </div>
      {error === null ? null : <ErrorAlert message={describeError(error)} />}
    </div>
  );
}
