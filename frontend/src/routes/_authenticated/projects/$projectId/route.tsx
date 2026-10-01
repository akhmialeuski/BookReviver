import { createFileRoute, Outlet } from '@tanstack/react-router';
import { useProjectEvents } from '@/features/projects/useProjectEvents';

/**
 * The layout of every screen of one book. It listens to the book's event stream for as long as any of them is
 * open, so the page of the book and the viewer both follow imports and page changes without a reload.
 */

export const Route = createFileRoute('/_authenticated/projects/$projectId')({
  component: BookLayout,
});

function BookLayout(): React.JSX.Element {
  const { projectId } = Route.useParams();
  useProjectEvents(projectId);
  return <Outlet />;
}
