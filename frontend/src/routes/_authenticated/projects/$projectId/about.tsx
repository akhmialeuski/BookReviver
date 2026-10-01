import { createFileRoute } from '@tanstack/react-router';
import { ProjectPage } from '@/features/projects/ProjectPage';
import { PageContainer } from '@/shared/ui/page-container';

/**
 * The description of one book, `/projects/<id>/about`, which the stage bar links to as "About the book".
 *
 * For now it shows the page of the book as it was; the task "Book workspace shell" replaces it with the sections of
 * the description, the cover and the deletion of the book.
 */

export const Route = createFileRoute('/_authenticated/projects/$projectId/about')({
  component: AboutRoute,
});

function AboutRoute(): React.JSX.Element {
  const { projectId } = Route.useParams();
  return (
    <PageContainer>
      <ProjectPage projectId={projectId} />
    </PageContainer>
  );
}
