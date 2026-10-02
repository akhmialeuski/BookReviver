import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import type { PageSchema, ProjectSchema } from '@/api';
import {
  listPagesApiV1ProjectsProjectIdPagesGetOptions,
  pageApiV1ProjectsProjectIdPagesPageIdGetOptions,
} from '@/api/@tanstack/react-query.gen';
import { PageThumbnail } from '@/features/pages/PageThumbnail';
import { nextStageOf, progressSegments } from '@/features/stages/progress';
import { STAGE_STATUS_TONE } from '@/features/stages/stages';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';
import { Card } from '@/shared/ui/card';

/**
 * One book of the library: its cover, title and author, the ten stages as a bar, and the stage it goes to next.
 *
 * The whole card opens the book, and the book opens on the stage it is at. The cover is the page the book names,
 * else its first page, which the list of books does not carry, so the card reads that page itself.
 */

/** Read the page the library shows as the cover of a book, or undefined while it loads or when there is none. */
function useCoverPage(project: ProjectSchema): PageSchema | undefined {
  const chosenId = project.cover_page_id;
  const chosen = useQuery({
    ...pageApiV1ProjectsProjectIdPagesPageIdGetOptions({
      path: { project_id: project.id, page_id: chosenId ?? '' },
    }),
    enabled: chosenId !== null,
  });
  const first = useQuery({
    ...listPagesApiV1ProjectsProjectIdPagesGetOptions({
      path: { project_id: project.id },
      query: { size: 1 },
    }),
    enabled: chosenId === null && project.page_count > 0,
  });
  return chosenId === null ? first.data?.items[0] : chosen.data;
}

export function ProjectCard({ project }: { project: ProjectSchema }): React.JSX.Element {
  const { details } = project;
  const cover = useCoverPage(project);
  const segments = progressSegments(project.progress);
  const done = segments.filter((segment) => segment.status === 'done').length;
  const next = nextStageOf(project);
  const byline =
    project.source_count === 0
      ? MESSAGES.library.noFiles
      : [
          details.primary_author,
          details.publication_year,
          MESSAGES.projects.counts.pages(project.page_count),
        ]
          .filter((part) => part !== '')
          .join(' · ');

  return (
    <Link
      to="/projects/$projectId"
      params={{ projectId: project.id }}
      data-testid="project-card"
      className="block h-full rounded-xl outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
    >
      <Card className="h-full gap-0 overflow-hidden py-0 transition-shadow hover:shadow-md">
        <div className="flex justify-center bg-muted/60 p-4">
          <div className="w-32">
            {cover === undefined ? (
              <div className="aspect-[3/4] rounded-md border bg-background" />
            ) : (
              <PageThumbnail page={cover} alt="" />
            )}
          </div>
        </div>
        <div className="grid gap-2 p-4">
          <h2 className="leading-snug font-semibold">
            {details.title || MESSAGES.projects.untitled}
          </h2>
          <p className="text-sm text-muted-foreground">{byline}</p>
          <div
            role="img"
            aria-label={MESSAGES.library.stages(done, segments.length)}
            data-testid="stage-progress"
            className="flex gap-1"
          >
            {segments.map((segment) => (
              <span
                key={segment.stage}
                data-testid="stage-segment"
                data-stage={segment.stage}
                data-status={segment.status}
                title={`${MESSAGES.stages.names[segment.stage]}: ${MESSAGES.stages.status[segment.status]}`}
                className={cn('h-1.5 flex-1 rounded-full', STAGE_STATUS_TONE[segment.status])}
              />
            ))}
          </div>
          <p className="flex items-baseline gap-1.5 text-sm" data-testid="next-stage">
            {next === null ? (
              <span className="text-muted-foreground">{MESSAGES.library.allDone}</span>
            ) : (
              <>
                <span className="sr-only">{MESSAGES.library.next}:</span>
                <span className="font-medium">{MESSAGES.stages.names[next.stage]}</span>
                <span className="text-muted-foreground">
                  · {MESSAGES.stages.status[next.status]}
                </span>
              </>
            )}
          </p>
        </div>
      </Card>
    </Link>
  );
}
