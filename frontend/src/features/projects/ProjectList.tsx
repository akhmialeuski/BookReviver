import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useEffect } from 'react';
import { listProjectsApiV1ProjectsGetOptions } from '@/api/@tanstack/react-query.gen';
import { CreateProjectDialog } from '@/features/projects/CreateProjectDialog';
import { ProjectCard } from '@/features/projects/ProjectCard';
import { pageToShow } from '@/features/projects/paging';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { ErrorAlert } from '@/shared/ui/error-alert';
import { Pager } from '@/shared/ui/pager';

/**
 * The books of the signed-in account, newest change first, a page at a time, with the button that creates one.
 */

const PAGE_SIZE = 12;

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
        <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {projects.data.items.map((project) => (
            <li key={project.id}>
              <ProjectCard project={project} />
            </li>
          ))}
        </ul>
        <Pager page={projects.data.page} pages={projects.data.pages} onPageChange={onPageChange} />
      </>
    );
  }

  return (
    <div className="grid gap-6">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-semibold tracking-tight">{MESSAGES.projects.title}</h1>
        <CreateProjectDialog />
      </div>
      {body}
    </div>
  );
}
