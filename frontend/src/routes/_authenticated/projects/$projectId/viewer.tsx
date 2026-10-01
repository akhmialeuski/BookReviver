import { createFileRoute } from '@tanstack/react-router';
import { parseViewerSearch } from '@/features/viewer/params';
import { ViewerPage } from '@/features/viewer/ViewerPage';

/**
 * The page viewer of one book, `/projects/<id>/viewer?page=<page id>&spread=true`. The page and the spread are in
 * the address, so any view can be linked and reloaded, and every page turn is a navigation that the back button undoes.
 */

export const Route = createFileRoute('/_authenticated/projects/$projectId/viewer')({
  validateSearch: parseViewerSearch,
  component: ViewerRoute,
});

function ViewerRoute(): React.JSX.Element {
  const { projectId } = Route.useParams();
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <ViewerPage
      projectId={projectId}
      search={search}
      onSearchChange={(next) => void navigate({ search: next })}
    />
  );
}
