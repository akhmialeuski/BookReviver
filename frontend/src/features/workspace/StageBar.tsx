import { useQuery } from '@tanstack/react-query';
import { Link, useMatch, useParams, useSearch } from '@tanstack/react-router';
import {
  CheckCircle2Icon,
  CircleXIcon,
  InfoIcon,
  Loader2Icon,
  TriangleAlertIcon,
} from 'lucide-react';
import type { ProjectSchema, Stage, StageStatus, StageSummarySchema } from '@/api';
import { projectApiV1ProjectsProjectIdGetOptions } from '@/api/@tanstack/react-query.gen';
import { parseStage } from '@/features/stages/parse';
import { PAGE_STATUS_TONE, STAGES, type StageEntry } from '@/features/stages/stages';
import { parseIdentifier } from '@/features/viewer/params';
import { stageProgress } from '@/features/workspace/progress';
import { useStageSummaries } from '@/features/workspace/queries';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';

/**
 * The bar of the ten stages above every screen of a book: four phases, and for each stage its number or state mark,
 * its name, and how far it has come.
 *
 * What is drawn comes from the summary of the stages and from the status of each stage in the book. A stage that
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

/** The line under the name of a stage: what it holds, or how far it has come. */
function StageNote({
  entry,
  summary,
  project,
}: {
  entry: StageEntry;
  summary: StageSummarySchema | undefined;
  project: ProjectSchema | undefined;
}): React.JSX.Element | null {
  const labels = MESSAGES.workspace.bar;
  if (summary === undefined || project === undefined) {
    return null;
  }
  if (!summary.available) {
    return (
      <span className="text-xs text-muted-foreground">{MESSAGES.stages.status.unavailable}</span>
    );
  }
  if (entry.stage === 'import') {
    return (
      <span className="text-xs text-muted-foreground">
        {project.source_count === 0
          ? labels.noFiles
          : labels.files(project.source_count, project.scan_count)}
      </span>
    );
  }
  if (project.page_count === 0) {
    return <span className="text-xs text-muted-foreground">{labels.waitsForPages}</span>;
  }
  if (summary.manual) {
    return (
      <span className="text-xs text-muted-foreground">{labels.pages(project.page_count)}</span>
    );
  }

  const progress = stageProgress(summary);
  return (
    <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
      <span
        className="flex h-1 w-10 overflow-hidden rounded-full bg-status-idle"
        aria-hidden="true"
      >
        {progress.segments.map((segment) => (
          <span
            key={segment.status}
            className={PAGE_STATUS_TONE[segment.status]}
            style={{ width: `${segment.percent}%` }}
          />
        ))}
      </span>
      <span>{labels.done(progress.done, progress.total)}</span>
      {progress.check > 0 ? (
        <span
          className="flex items-center gap-0.5 text-status-attention"
          title={labels.check(progress.check)}
        >
          <TriangleAlertIcon className="size-3" aria-hidden="true" />
          {progress.check}
          <span className="sr-only">{labels.check(progress.check)}</span>
        </span>
      ) : null}
      {progress.failed > 0 ? (
        <span
          className="flex items-center gap-0.5 text-status-failed"
          title={labels.failed(progress.failed)}
        >
          <CircleXIcon className="size-3" aria-hidden="true" />
          {progress.failed}
          <span className="sr-only">{labels.failed(progress.failed)}</span>
        </span>
      ) : null}
    </span>
  );
}

export function StageBar({ projectId }: { projectId: string }): React.JSX.Element {
  const project = useQuery(
    projectApiV1ProjectsProjectIdGetOptions({ path: { project_id: projectId } }),
  );
  const summaries = useStageSummaries(projectId);
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
                  <StageMark number={STAGES.indexOf(entry) + 1} status={status} />
                  <span className="grid text-left">
                    <span className="text-sm leading-tight font-medium">
                      {MESSAGES.stages.names[entry.stage]}
                    </span>
                    {status === 'unavailable' ? null : (
                      <span className="sr-only">{MESSAGES.stages.status[status]}</span>
                    )}
                    <StageNote
                      entry={entry}
                      summary={summaryOf(entry.stage)}
                      project={project.data}
                    />
                  </span>
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
