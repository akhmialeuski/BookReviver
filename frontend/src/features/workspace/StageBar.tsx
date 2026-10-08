import { useQuery } from '@tanstack/react-query';
import { Link, useMatch, useParams, useSearch } from '@tanstack/react-router';
import { CheckCircle2Icon, InfoIcon, ListIcon, Loader2Icon } from 'lucide-react';
import type { Stage, StageStatus, StageSummarySchema } from '@/api';
import { projectApiV1ProjectsProjectIdGetOptions } from '@/api/@tanstack/react-query.gen';
import { parseStage } from '@/features/stages/parse';
import { STAGES, type StageEntry } from '@/features/stages/stages';
import { parseIdentifier } from '@/features/viewer/params';
import { useStageSummaries } from '@/features/workspace/queries';
import { useIsNarrow } from '@/shared/hooks/useMediaQuery';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';

/**
 * The bar of the ten stages above every screen of a book: four phases, and for each stage its number or state mark and
 * its name. The counts of the pages live in the strip and the panel of the open stage, not here.
 *
 * What is drawn comes from the status of each stage in the book and from the summary of the stages. A stage that
 * cannot be worked in yet says "Soon" but still opens, with its panel and an empty canvas. The state of a stage is
 * never carried by colour alone: it has a mark, and the word is read out. The "About the book" tab ends the bar.
 */

const PHASES = [...new Set(STAGES.map((entry) => entry.phase))];

function StageMark({ number, status }: { number: number; status: StageStatus }): React.JSX.Element {
  if (status === 'done') {
    return <CheckCircle2Icon className="size-5 shrink-0 text-status-done" aria-hidden="true" />;
  }
  if (status === 'running') {
    return (
      <Loader2Icon
        className="size-5 shrink-0 text-status-running motion-safe:animate-spin"
        aria-hidden="true"
      />
    );
  }
  return (
    <span
      className={cn(
        'flex size-5 shrink-0 items-center justify-center rounded-full border text-xs',
        status === 'attention' && 'border-status-attention text-status-attention',
      )}
      aria-hidden="true"
    >
      {number}
    </span>
  );
}

/** A stage as the bar writes it: its mark, its name, the word for its state read out, and "Soon" for a stage not open yet. */
function StageBody({
  entry,
  status,
  summary,
}: {
  entry: StageEntry;
  status: StageStatus;
  summary: StageSummarySchema | undefined;
}): React.JSX.Element {
  return (
    <>
      <StageMark number={STAGES.indexOf(entry) + 1} status={status} />
      <span className="grid text-left">
        <span className="text-sm leading-tight font-medium">
          {MESSAGES.stages.names[entry.stage]}
        </span>
        {status === 'unavailable' ? null : (
          <span className="sr-only">{MESSAGES.stages.status[status]}</span>
        )}
        {summary?.available === false ? (
          <span className="text-xs text-muted-foreground">
            {MESSAGES.stages.status.unavailable}
          </span>
        ) : null}
      </span>
    </>
  );
}

export function StageBar({ projectId }: { projectId: string }): React.JSX.Element {
  const project = useQuery(
    projectApiV1ProjectsProjectIdGetOptions({ path: { project_id: projectId } }),
  );
  const summaries = useStageSummaries(projectId);
  const narrow = useIsNarrow();
  const { stage } = useParams({ strict: false });
  const page = parseIdentifier(useSearch({ strict: false }).page);
  const current = parseStage(stage);
  const onAbout =
    useMatch({ from: '/_authenticated/projects/$projectId/about', shouldThrow: false }) !==
    undefined;

  const summaryOf = (key: Stage): StageSummarySchema | undefined =>
    summaries.data?.find((summary) => summary.stage === key);
  const statusOf = (key: Stage): StageStatus =>
    project.data?.progress.find((entry) => entry.stage === key)?.status ?? 'waiting';

  if (narrow) {
    const currentEntry = onAbout ? undefined : STAGES.find((entry) => entry.stage === current);
    const itemClass =
      'flex items-center gap-2 outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50';
    return (
      <nav
        aria-label={MESSAGES.workspace.bar.label}
        className="flex shrink-0 items-center justify-between gap-2 border-b px-3 py-1.5"
        data-testid="stage-bar"
      >
        {currentEntry === undefined ? (
          <span className={cn(itemClass, 'text-sm font-medium')}>
            {onAbout ? MESSAGES.workspace.bar.about : null}
          </span>
        ) : (
          <Link
            to="/projects/$projectId/stages/$stage"
            params={{ projectId, stage: currentEntry.stage }}
            search={page === undefined ? {} : { page }}
            aria-current="page"
            data-testid={`stage-${currentEntry.stage}`}
            data-status={statusOf(currentEntry.stage)}
            className={cn(itemClass, 'min-w-0 rounded-md px-1')}
          >
            <StageBody
              entry={currentEntry}
              status={statusOf(currentEntry.stage)}
              summary={summaryOf(currentEntry.stage)}
            />
          </Link>
        )}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm" data-testid="stage-menu">
              <ListIcon aria-hidden="true" />
              {MESSAGES.workspace.bar.otherStages}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" data-testid="stage-menu-list">
            {STAGES.filter((entry) => entry.stage !== currentEntry?.stage).map((entry) => {
              const status = statusOf(entry.stage);
              return (
                <DropdownMenuItem key={entry.stage} asChild>
                  <Link
                    to="/projects/$projectId/stages/$stage"
                    params={{ projectId, stage: entry.stage }}
                    search={page === undefined ? {} : { page }}
                    data-testid={`stage-${entry.stage}`}
                    data-status={status}
                    className={cn(status === 'unavailable' && 'text-muted-foreground')}
                  >
                    <StageBody entry={entry} status={status} summary={summaryOf(entry.stage)} />
                  </Link>
                </DropdownMenuItem>
              );
            })}
            {onAbout ? null : (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem asChild>
                  <Link
                    to="/projects/$projectId/about"
                    params={{ projectId }}
                    data-testid="stage-about"
                  >
                    <InfoIcon aria-hidden="true" />
                    {MESSAGES.workspace.bar.about}
                  </Link>
                </DropdownMenuItem>
              </>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </nav>
    );
  }

  return (
    <nav
      aria-label={MESSAGES.workspace.bar.label}
      className="flex shrink-0 items-stretch overflow-x-auto border-b"
      data-testid="stage-bar"
    >
      {PHASES.map((phase) => (
        <div key={phase} className="flex shrink-0 flex-col border-r px-1 pt-1.5 first:pl-3">
          <span className="px-2 text-[0.65rem] font-medium tracking-wider text-muted-foreground uppercase">
            {MESSAGES.stages.phases[phase]}
          </span>
          <div className="flex">
            {STAGES.filter((entry) => entry.phase === phase).map((entry) => {
              const status = statusOf(entry.stage);
              const isCurrent = current === entry.stage && !onAbout;
              return (
                <Link
                  key={entry.stage}
                  to="/projects/$projectId/stages/$stage"
                  params={{ projectId, stage: entry.stage }}
                  search={page === undefined ? {} : { page }}
                  aria-current={isCurrent ? 'page' : undefined}
                  data-testid={`stage-${entry.stage}`}
                  data-status={status}
                  className={cn(
                    'flex min-w-28 items-center gap-2 border-b-2 px-2 py-1.5 outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50',
                    isCurrent
                      ? 'border-primary bg-accent/50'
                      : 'border-transparent hover:bg-accent/40',
                    status === 'unavailable' && 'text-muted-foreground',
                  )}
                >
                  <StageBody entry={entry} status={status} summary={summaryOf(entry.stage)} />
                </Link>
              );
            })}
          </div>
        </div>
      ))}
      <Link
        to="/projects/$projectId/about"
        params={{ projectId }}
        aria-current={onAbout ? 'page' : undefined}
        data-testid="stage-about"
        className={cn(
          'ml-auto flex shrink-0 items-center gap-2 self-end border-b-2 px-4 py-2.5 text-sm font-medium outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50',
          onAbout ? 'border-primary bg-accent/50' : 'border-transparent hover:bg-accent/40',
        )}
      >
        <InfoIcon className="size-4" aria-hidden="true" />
        {MESSAGES.workspace.bar.about}
      </Link>
    </nav>
  );
}
