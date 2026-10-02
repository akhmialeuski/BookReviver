import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useEffect } from 'react';
import { listProjectsApiV1ProjectsGetOptions } from '@/api/@tanstack/react-query.gen';
import { CreateProjectDialog } from '@/features/projects/CreateProjectDialog';
import { ProjectCard } from '@/features/projects/ProjectCard';
import { pageToShow } from '@/features/projects/paging';
import { STAGE_STATUS_TONE } from '@/features/stages/stages';
import { describeError } from '@/shared/http/problem';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';
import { ErrorAlert } from '@/shared/ui/error-alert';
import { Pager } from '@/shared/ui/pager';

/**
 * The books of the signed-in account, newest change first, a page at a time, with the button that creates one.
 *
 * Under the cards a key names the colours of the bar every card draws for the stages of its book.
 */

const PAGE_SIZE = 12;

/** The states the key under the cards names, which are the ones a stage of a started book is in. */
const LEGEND = ['done', 'attention', 'running', 'waiting'] as const;

export function ProjectList({
  page,
  onPageChange,
}: {
  page: number;
  /** `replace` asks for the history entry to be replaced, as for a correction the person did not make. */
  onPageChange: (page: number, replace?: boolean) => void;
}): React.JSX.Element {
  const projects = useQuery({
    ...listProjectsApiV1ProjectsGetOptions({ query: { page, size: PAGE_SIZE } }),
    placeholderData: keepPreviousData,
  });

  // The address can name a page that is gone, for instance after the last book of the last page is deleted
  const pages = projects.isPlaceholderData ? undefined : projects.data?.pages;
  const target = pages === undefined ? page : pageToShow(page, pages);
  useEffect(() => {
    if (target !== page) {
      onPageChange(target, true);
    }
  }, [target, page, onPageChange]);

  let body: React.JSX.Element;
  if (projects.isError) {
    body = <ErrorAlert message={describeError(projects.error)} />;
  } else if (projects.data === undefined) {
    body = <p className="text-sm text-muted-foreground">{MESSAGES.common.loading}</p>;
  } else if (projects.data.items.length === 0) {
    body = <p className="text-sm text-muted-foreground">{MESSAGES.projects.empty}</p>;
  } else {
    body = (
      <>
        <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {projects.data.items.map((project) => (
            <li key={project.id}>
              <ProjectCard project={project} />
            </li>
          ))}
        </ul>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted-foreground">
          <span>{MESSAGES.library.legend}</span>
          {LEGEND.map((status) => (
            <span key={status} className="inline-flex items-center gap-1.5">
              <span className={cn('h-1.5 w-4 rounded-full', STAGE_STATUS_TONE[status])} />
              {MESSAGES.stages.status[status]}
            </span>
          ))}
        </div>
        <Pager page={projects.data.page} pages={projects.data.pages} onPageChange={onPageChange} />
      </>
    );
  }

  return (
    <div className="grid gap-6">
      <div className="flex items-center justify-between gap-4">
        <div className="grid gap-1">
          <h1 className="text-2xl font-semibold tracking-tight">{MESSAGES.projects.title}</h1>
          {projects.data === undefined ? null : (
            <p className="text-sm text-muted-foreground">
              {MESSAGES.library.count(projects.data.total)}
            </p>
          )}
        </div>
        <CreateProjectDialog />
      </div>
      {body}
    </div>
  );
}
