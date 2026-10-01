import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { listProjectsApiV1ProjectsGetOptions } from '@/api/@tanstack/react-query.gen';
import { CreateProjectDialog } from '@/features/projects/CreateProjectDialog';
import { ProjectCard } from '@/features/projects/ProjectCard';
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
  onPageChange: (page: number) => void;
}): React.JSX.Element {
  const projects = useQuery({
    ...listProjectsApiV1ProjectsGetOptions({ query: { page, size: PAGE_SIZE } }),
    placeholderData: keepPreviousData,
  });

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
