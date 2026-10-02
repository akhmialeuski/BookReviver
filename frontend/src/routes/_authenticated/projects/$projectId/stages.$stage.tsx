import { createFileRoute, notFound } from '@tanstack/react-router';
import { useEffect } from 'react';
import { parseStage } from '@/features/stages/parse';
import { parseStageSearch } from '@/features/workspace/params';
import { rememberStage } from '@/features/workspace/storage';
import { MESSAGES } from '@/shared/messages';

/**
 * One stage of one book, `/projects/<id>/stages/<stage>?page=&scan=&view=&compare=&filter=`.
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
  const known = parseStage(stage);

  useEffect(() => {
    if (known !== null) {
      rememberStage(projectId, known);
    }
  }, [projectId, known]);

  return <h1>{stage}</h1>;
}
