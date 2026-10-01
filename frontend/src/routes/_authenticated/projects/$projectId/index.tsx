import { createFileRoute } from '@tanstack/react-router';
import { ProjectPage } from '@/features/projects/ProjectPage';
import { PageContainer } from '@/shared/ui/page-container';

/**
 * The page of one book, `/projects/<id>`.
 */

export const Route = createFileRoute('/_authenticated/projects/$projectId/')({
  component: BookRoute,
});

function BookRoute(): React.JSX.Element {
  const { projectId } = Route.useParams();
  return (
    <PageContainer>
      <ProjectPage projectId={projectId} />
    </PageContainer>
  );
}
