import { useQuery, useQueryClient } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';
import { useMemo } from 'react';
import { projectApiV1ProjectsProjectIdGetOptions } from '@/api/@tanstack/react-query.gen';
import type { PlaceAddress } from '@/features/place/address';
import { loadPlace } from '@/features/place/open';
import { PlaceWriterContext, useBookPlaceWriter } from '@/features/place/PlaceWriterContext';
import { cachedPlace } from '@/features/place/queries';
import { parseStage, startStage } from '@/features/stages/parse';
import { parseViewerSearch } from '@/features/viewer/params';
import { ViewerPage } from '@/features/viewer/ViewerPage';

/**
 * The page viewer of one book, `/projects/<id>/viewer?page=<page id>&spread=true`. The page and the spread are in
 * the address, so any view can be linked and reloaded, and every page turn is a navigation that the back button undoes.
 * The screen keeps the place of the reader in the book as they read, so the book opens here again. The place also holds
 * the stage the reader came from, which the way back leads to.
 */

export const Route = createFileRoute('/_authenticated/projects/$projectId/viewer')({
  validateSearch: parseViewerSearch,
  loader: ({ context, params }) => loadPlace(context.queryClient, params.projectId),
  component: ViewerRoute,
});

function ViewerRoute(): React.JSX.Element {
  const { projectId } = Route.useParams();
  // The place belongs to one book, so another book gets a screen of its own
  return <ViewerOfBook key={projectId} projectId={projectId} />;
}

function ViewerOfBook({ projectId }: { projectId: string }): React.JSX.Element {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const queryClient = useQueryClient();
  const project = useQuery(
    projectApiV1ProjectsProjectIdGetOptions({ path: { project_id: projectId } }),
  );
  // The stage the reader worked on before reading is in the place the workspace wrote, else the book names one
  const left = cachedPlace(queryClient, projectId);
  const stage = startStage(
    left === null ? null : parseStage(left.stage),
    project.data?.next_stage ?? null,
  );
  const address = useMemo<PlaceAddress>(
    () => ({
      mode: 'reading',
      stage,
      ...(search.page === undefined ? {} : { page: search.page }),
      ...(search.spread === true ? { view: 'spread' as const } : {}),
    }),
    [stage, search.page, search.spread],
  );
  const writer = useBookPlaceWriter(projectId, address);
  return (
    <PlaceWriterContext value={writer}>
      <ViewerPage
        projectId={projectId}
        stage={stage}
        search={search}
        onSearchChange={(next) => void navigate({ search: next })}
      />
    </PlaceWriterContext>
  );
}
