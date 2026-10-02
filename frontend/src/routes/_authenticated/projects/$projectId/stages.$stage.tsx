import { createFileRoute, notFound } from '@tanstack/react-router';
import { useEffect } from 'react';
import { ImportScreen } from '@/features/import/ImportScreen';
import { OrderScreen } from '@/features/order/OrderScreen';
import { parseStage } from '@/features/stages/parse';
import { parseStageSearch, type StageSearch } from '@/features/workspace/params';
import { StageScreen } from '@/features/workspace/StageScreen';
import { rememberStage } from '@/features/workspace/storage';
import { MESSAGES } from '@/shared/messages';

/**
 * One stage of one book, `/projects/<id>/stages/<stage>?page=&scan=&source=&view=&compare=&filter=`.
 *
 * The stage is a segment of the path and the rest of the view is in the search params, so any view can be linked and
 * reloaded. A segment that names no stage answers with the not-found screen inside the layout of the book, which
 * keeps the header and the stage bar on screen.
 */

export const Route = createFileRoute('/_authenticated/projects/$projectId/stages/$stage')({
  validateSearch: parseStageSearch,
  beforeLoad: ({ params }) => {
    if (parseStage(params.stage) === null) {
      throw notFound();
    }
  },
  notFoundComponent: StageNotFound,
  component: StageRoute,
});

function StageNotFound(): React.JSX.Element {
  return <p className="p-6 text-sm text-muted-foreground">{MESSAGES.workspace.unknownStage}</p>;
}

function StageRoute(): React.JSX.Element {
  const { projectId, stage } = Route.useParams();
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const known = parseStage(stage);

  useEffect(() => {
    if (known !== null) {
      rememberStage(projectId, known);
    }
  }, [projectId, known]);

  if (known === null) {
    return <StageNotFound />;
  }
  const onSearchChange = (changes: Partial<StageSearch>): void =>
    void navigate({ search: (previous) => ({ ...previous, ...changes }) });
  // Import works on files and scans, not on pages, so it has a screen of its own
  if (known === 'import') {
    return <ImportScreen projectId={projectId} search={search} onSearchChange={onSearchChange} />;
  }
  // The Order stage is a grid of pages with its own panel, not a strip, a canvas and a recipe
  if (known === 'page-order') {
    return <OrderScreen projectId={projectId} search={search} onSearchChange={onSearchChange} />;
  }
  return (
    <StageScreen
      projectId={projectId}
      stage={known}
      search={search}
      onSearchChange={onSearchChange}
    />
  );
}
