import { createFileRoute, notFound } from '@tanstack/react-router';
import { useMemo } from 'react';
import { ImportScreen } from '@/features/import/ImportScreen';
import { OrderScreen } from '@/features/order/OrderScreen';
import type { PlaceAddress } from '@/features/place/address';
import { loadPlace } from '@/features/place/open';
import { PlaceWriterContext, useBookPlaceWriter } from '@/features/place/PlaceWriterContext';
import { parseStage } from '@/features/stages/parse';
import { parseStageSearch, type StageSearch } from '@/features/workspace/params';
import { StageScreen } from '@/features/workspace/StageScreen';
import { MESSAGES } from '@/shared/messages';

/**
 * One stage of one book, `/projects/<id>/stages/<stage>?page=&scan=&source=&view=&compare=&filter=`.
 *
 * The stage is a segment of the path and the rest of the view is in the search params, so any view can be linked and
 * reloaded. A segment that names no stage answers with the not-found screen inside the layout of the book, which
 * keeps the header and the stage bar on screen. The screen keeps the place of the reader in the book as they move, so
 * the book opens here again.
 */

export const Route = createFileRoute('/_authenticated/projects/$projectId/stages/$stage')({
  validateSearch: parseStageSearch,
  beforeLoad: ({ params }) => {
    if (parseStage(params.stage) === null) {
      throw notFound();
    }
  },
  loader: ({ context, params }) => loadPlace(context.queryClient, params.projectId),
  notFoundComponent: StageNotFound,
  component: StageRoute,
});

function StageNotFound(): React.JSX.Element {
  return <p className="p-6 text-sm text-muted-foreground">{MESSAGES.workspace.unknownStage}</p>;
}

function StageRoute(): React.JSX.Element {
  const { projectId } = Route.useParams();
  // The place belongs to one book, so another book gets a screen of its own
  return <StageOfBook key={projectId} projectId={projectId} />;
}

function StageOfBook({ projectId }: { projectId: string }): React.JSX.Element {
  const { stage } = Route.useParams();
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const known = parseStage(stage);
  const address = useMemo<PlaceAddress | null>(
    () => (known === null ? null : { mode: 'workspace', stage: known, ...search }),
    [known, search],
  );
  const writer = useBookPlaceWriter(projectId, address);

  if (known === null) {
    return <StageNotFound />;
  }
  // The stage is named in the call, because a move made while the router is already on its way to a screen that has no
  // stage, such as the reading mode that is still loading, would otherwise read the stage from there and go to `undefined`
  const onSearchChange = (changes: Partial<StageSearch>): void =>
    void navigate({
      params: (previous) => ({ ...previous, stage: known }),
      search: (previous) => ({ ...previous, ...changes }),
    });
  let screen = (
    <StageScreen
      projectId={projectId}
      stage={known}
      search={search}
      onSearchChange={onSearchChange}
    />
  );
  // Import works on files and scans, and the Order stage is a grid of pages with its own panel
  if (known === 'import') {
    screen = <ImportScreen projectId={projectId} search={search} onSearchChange={onSearchChange} />;
  } else if (known === 'page-order') {
    screen = <OrderScreen projectId={projectId} search={search} onSearchChange={onSearchChange} />;
  }
  return <PlaceWriterContext value={writer}>{screen}</PlaceWriterContext>;
}
