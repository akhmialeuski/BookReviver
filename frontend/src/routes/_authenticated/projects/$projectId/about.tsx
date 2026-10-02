import { createFileRoute } from '@tanstack/react-router';
import { AboutPage } from '@/features/about/AboutPage';

/**
 * The description of one book, `/projects/<id>/about`, which the stage bar links to as "About the book".
 *
 * It fills the width under the stage bar, so it does not sit in the centred column of the other screens.
 */

export const Route = createFileRoute('/_authenticated/projects/$projectId/about')({
  component: AboutRoute,
});

function AboutRoute(): React.JSX.Element {
  const { projectId } = Route.useParams();
  return <AboutPage projectId={projectId} />;
}
