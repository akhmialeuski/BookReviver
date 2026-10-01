import { createFileRoute } from '@tanstack/react-router';
import { ProjectPage } from '@/features/projects/ProjectPage';

/**
 * The page of one book, `/projects/<id>`.
 */

export const Route = createFileRoute('/_authenticated/projects/$projectId')({
  component: BookRoute,
});

function BookRoute(): React.JSX.Element {
  const { projectId } = Route.useParams();
  return <ProjectPage projectId={projectId} />;
}
