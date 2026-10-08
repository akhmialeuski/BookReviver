import { ChevronDownIcon, LoaderCircleIcon, PlayIcon } from 'lucide-react';
import { useState } from 'react';
import type { RunMode } from '@/api';
import { OverwriteDialog } from '@/features/processing/OverwriteDialog';
import { useOwnWork } from '@/features/processing/queries';
import { hasResult, type PageGroup, RunScope, troubleOf } from '@/features/processing/scope';
import { canRunThrough } from '@/features/processing/stepRuns';
import { UnsplitDialog, UnsplitQuestion } from '@/features/processing/UnsplitDialog';
import type { Processing } from '@/features/processing/useProcessing';
import type { StageRun } from '@/features/processing/useStageRun';
import { useActiveJobs, useStageSummaries } from '@/features/workspace/queries';
import type { BarStep } from '@/features/workspace/steps';
import type { StripItem } from '@/features/workspace/strip';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The foot of the panel of a processing stage, which is the only place a run of the stage starts from: how many pages
 * are out of date or failed, and one button that says what the run will do.
 *
 * The menu of the button chooses the pages, how far the run goes and what it does with the pages that have work of their
 * own. The text of the button follows the choice: "Run" when none of the pages has a result yet, "Run again" when some
 * has, then the pages and the step the run goes through.
 *
 * With the active recipe shown the run names no recipe, so each page is processed by the variant pinned to it or the
 * rule of the book that matches it. With another variant shown the run is a trial of that variant on the pages.
 *
 * A run goes by the saved recipe, so while the draft has changes the run waits for them to be saved. A run that would
 * send a scan back to one page asks first, and carries the confirmation the server wants. The pages a run through some
 * of the steps left short of the last say so, by the step. A run that drops the work of the pages says how many pages
 * lose work first.
 */

const labels = MESSAGES.processing;
const menu = labels.runMenu;

/** How far the run goes. */
const Through = {
  Open: 'open',
  Stage: 'stage',
} as const;

type Through = (typeof Through)[keyof typeof Through];

/** What the run does with the pages that have work of their own, in the order the menu lists them. */
const OWN_MODES = ['keep', 'skip-own-work', 'drop-own-work'] as const satisfies readonly RunMode[];

type OwnMode = (typeof OWN_MODES)[number];

/** The names of the groups of pages the menu lists. */
const GROUP_NAMES: Record<PageGroup, string> = {
  ...MESSAGES.pages.contentTypes,
  blank: MESSAGES.pages.kinds.blank,
};

/** Write a choice of the pages for the menu. */
function pageLabelOf(scope: RunScope, group: PageGroup | undefined, pageLabel: string): string {
  switch (scope) {
    case RunScope.Page:
      return menu.page(pageLabel);
    case RunScope.FromPage:
      return menu.fromPage;
    case RunScope.Selected:
      return menu.selected;
    case RunScope.Group:
      return `${menu.group} · ${GROUP_NAMES[group ?? 'text']}`;
    case RunScope.Attention:
      return menu.attention;
    case RunScope.All:
      return menu.all;
  }
}

export function RunControls({
  processing,
  items,
  run,
  openStep,
}: {
  processing: Processing;
  items: readonly StripItem[];
  run: StageRun;
  /** The step that is open in the bar of the steps, or undefined for a stage that has none. */
  openStep?: BarStep;
}): React.JSX.Element {
  const { projectId, stage, recipe, catalogue } = processing;
  const summaries = useStageSummaries(projectId);
  const trouble = troubleOf(items);
  const stopped = summaries.data?.find((entry) => entry.stage === stage)?.stopped ?? [];
  // A run of this stage that is still going says so, since its pages read up to date before it has placed them all
  const running = useActiveJobs(projectId).data?.find(
    (job) => job.kind === 'run-stage' && job.stage === stage,
  );
  const [menuOpen, setMenuOpen] = useState(false);
  const [pageKey, setPageKey] = useState<string>(RunScope.Attention);
  const [through, setThrough] = useState<Through>(Through.Stage);
  const [ownMode, setOwnMode] = useState<OwnMode>('keep');

  // The pages, each with a key the radio group holds, since a group of pages has no scope of its own
  const pages = run.choices.map((entry) => ({
    ...entry,
    key: entry.group === undefined ? entry.scope : `${entry.scope}:${entry.group}`,
    label: pageLabelOf(entry.scope, entry.group, run.pageLabel),
  }));
  const chosen =
    pages.find((entry) => entry.key === pageKey) ??
    pages.find((entry) => entry.scope === RunScope.Attention);

  // How far the run goes: up to the step open in the bar or the list of the recipe, or through the last step that is on
  const steps = recipe?.steps ?? [];
  const draftIndex = processing.steps.findIndex((step) => step.id === processing.openId);
  const openIndex = openStep?.index ?? (draftIndex < 0 ? undefined : draftIndex);
  const lastIndex = steps.findLastIndex((step) => step.enabled);
  const canStopAtOpen =
    openIndex !== undefined && steps.length > 1 && canRunThrough(steps, openIndex);
  const throughStep = through === Through.Open && canStopAtOpen ? openIndex : undefined;
  const titleOf = (index: number | undefined): string => {
    const key = index === undefined ? undefined : steps[index]?.processor_key;
    return key === undefined ? '' : (catalogue.find((entry) => entry.key === key)?.title ?? key);
  };
  const stepName = (index: number | undefined): string =>
    index === undefined ? '' : menu.step(index + 1, titleOf(index));

  // The pages that have work of their own are counted by the server, over the pages and the steps of this choice
  const request = { scope: chosen?.scope ?? RunScope.Attention, group: chosen?.group, throughStep };
  const own = useOwnWork(
    projectId,
    stage,
    menuOpen || ownMode !== 'keep' ? run.bodyOf(request) : null,
  ).data?.own_pages;
  const mode: RunMode = own === 0 ? 'keep' : ownMode;
  const runs = (chosen?.items.length ?? 0) - (mode === 'skip-own-work' ? (own ?? 0) : 0);

  const pagesText = (): string => {
    switch (chosen?.scope) {
      case RunScope.Page:
        return menu.onPage;
      case RunScope.FromPage:
        return menu.onFromPage(runs);
      case RunScope.Selected:
        return menu.onSelected(runs);
      case RunScope.Group:
        return menu.onGroup(runs, GROUP_NAMES[chosen.group ?? 'text']);
      case RunScope.All:
        return menu.onAll(runs);
      default:
        return menu.onAttention(runs, trouble.stale, trouble.failed);
    }
  };
  const buttonText = `${chosen?.items.some(hasResult) ? menu.again : menu.first} ${pagesText()} ${menu.through(titleOf(throughStep ?? lastIndex))}`;

  return (
    <div className="grid grid-cols-1 gap-3">
      {stopped.length === 0 || recipe === undefined ? null : (
        <ul className="grid gap-0.5 text-sm" data-testid="run-stopped">
          {stopped.map(({ through_step: step, pages }) => (
            <li key={step}>{labels.footer.stoppedAt(step + 1, recipe.steps.length, pages)}</li>
          ))}
        </ul>
      )}
      <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm" data-testid="run-summary">
        {running !== undefined ? (
          <span className="flex items-center gap-1.5" data-testid="run-summary-running">
            <LoaderCircleIcon className="size-3.5 animate-spin" aria-hidden="true" />
            {labels.footer.running(running.progress.done, running.progress.total)}
          </span>
        ) : trouble.stale === 0 && trouble.failed === 0 ? (
          // Pages that stopped short are up to date but not done, which the lines above say
          stopped.length === 0 && (
            <span className="text-muted-foreground">{labels.footer.allClear}</span>
          )
        ) : (
          <>
            {trouble.stale === 0 ? null : (
              <span className="flex items-center gap-1.5">
                <span className="size-2 rounded-full bg-status-attention" aria-hidden="true" />
                {labels.footer.outOfDate(trouble.stale)}
              </span>
            )}
            {trouble.failed === 0 ? null : (
              <span className="flex items-center gap-1.5">
                <span className="size-2 rounded-full bg-status-failed" aria-hidden="true" />
                {labels.footer.failed(trouble.failed)}
              </span>
            )}
          </>
        )}
      </p>
      {processing.dirty ? (
        <p className="text-xs text-muted-foreground">{labels.save.saveFirst}</p>
      ) : run.busy ? (
        <p className="text-xs text-muted-foreground">{labels.footer.busy}</p>
      ) : null}
      <div className="flex">
        <Button
          className="min-w-0 flex-1 justify-start rounded-r-none"
          disabled={run.disabled || runs <= 0}
          title={buttonText}
          data-testid="run-start"
          onClick={() => run.start({ ...request, mode })}
        >
          {run.pending ? <LoaderCircleIcon className="animate-spin" /> : <PlayIcon />}
          <span className="truncate">{buttonText}</span>
        </Button>
        <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
          <DropdownMenuTrigger asChild>
            <Button
              size="icon"
              className="rounded-l-none border-l border-primary-foreground/30"
              disabled={run.disabled}
              aria-label={menu.menu}
              data-testid="run-menu"
            >
              <ChevronDownIcon />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            side="top"
            align="end"
            className="max-h-(--radix-dropdown-menu-content-available-height) w-80 overflow-y-auto"
          >
            <DropdownMenuLabel className="text-xs text-muted-foreground uppercase">
              {menu.pages}
            </DropdownMenuLabel>
            <DropdownMenuRadioGroup value={chosen?.key} onValueChange={setPageKey}>
              {pages.map((entry) => (
                <DropdownMenuRadioItem
                  key={entry.key}
                  value={entry.key}
                  disabled={entry.items.length === 0}
                  data-testid={`run-pages-${entry.key}`}
                  onSelect={(event) => event.preventDefault()}
                >
                  <span>{entry.label}</span>
                  <span className="text-muted-foreground">{entry.items.length}</span>
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
            {canStopAtOpen ? (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuLabel className="text-xs text-muted-foreground uppercase">
                  {menu.throughLabel}
                </DropdownMenuLabel>
                <DropdownMenuRadioGroup
                  value={throughStep === undefined ? Through.Stage : Through.Open}
                  onValueChange={(next) =>
                    setThrough(next === Through.Open ? Through.Open : Through.Stage)
                  }
                >
                  <DropdownMenuRadioItem
                    value={Through.Open}
                    data-testid="run-through-open"
                    onSelect={(event) => event.preventDefault()}
                  >
                    {menu.throughOpen(stepName(openIndex))}
                  </DropdownMenuRadioItem>
                  <DropdownMenuRadioItem
                    value={Through.Stage}
                    data-testid="run-through-stage"
                    onSelect={(event) => event.preventDefault()}
                  >
                    {menu.throughAll(stepName(lastIndex))}
                  </DropdownMenuRadioItem>
                </DropdownMenuRadioGroup>
              </>
            ) : null}
            {own === undefined || own === 0 ? null : (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuLabel
                  className="text-xs text-muted-foreground uppercase"
                  title={labels.modes.hint}
                >
                  {labels.modes.label(own)}
                </DropdownMenuLabel>
                <DropdownMenuRadioGroup
                  value={mode}
                  onValueChange={(next) =>
                    setOwnMode(OWN_MODES.find((option) => option === next) ?? 'keep')
                  }
                >
                  {OWN_MODES.map((option) => (
                    <DropdownMenuRadioItem
                      key={option}
                      value={option}
                      data-testid={`run-own-${option}`}
                      onSelect={(event) => event.preventDefault()}
                    >
                      <span>{labels.modes.options[option]}</span>
                      {option === 'skip-own-work' ? (
                        <span className="text-muted-foreground">{labels.modes.left(own)}</span>
                      ) : null}
                    </DropdownMenuRadioItem>
                  ))}
                </DropdownMenuRadioGroup>
              </>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      {run.error === null ? null : <ErrorAlert message={describeError(run.error)} />}
      <OverwriteDialog
        impact={run.overwriting}
        onConfirm={run.confirmOverwrite}
        onCancel={run.cancelOverwrite}
      />
      <UnsplitDialog
        question={run.confirming ? UnsplitQuestion.One : null}
        onCancel={run.cancel}
        onConfirm={run.confirm}
      />
    </div>
  );
}
