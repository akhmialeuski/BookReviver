import { createFileRoute } from '@tanstack/react-router';
import { AboutPage } from '@/features/about/AboutPage';

/**
 * The description of one book, `/books/<id>/about`, which the stage bar links to as "About the book".
 *
 * It fills the width under the stage bar, so it does not sit in the centred column of the other screens.
 */

export const Route = createFileRoute('/_authenticated/books/$bookId/about')({
  component: AboutRoute,
});

function AboutRoute(): React.JSX.Element {
  const { bookId } = Route.useParams();
  return <AboutPage bookId={bookId} />;
}
