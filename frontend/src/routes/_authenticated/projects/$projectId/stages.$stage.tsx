import { createFileRoute, notFound, useParams } from '@tanstack/react-router';
import { useMemo } from 'react';
import type { Stage } from '@/api';
import { ImportScreen } from '@/features/import/ImportScreen';
import { OrderScreen } from '@/features/order/OrderScreen';
import type { PlaceAddress } from '@/features/place/address';
import { loadPlace } from '@/features/place/open';
import { PlaceWriterContext, useBookPlaceWriter } from '@/features/place/PlaceWriterContext';
import { parseStage } from '@/features/stages/parse';
import { parseIdentifier } from '@/features/viewer/params';
import { parseStageSearch, type StageSearch } from '@/features/workspace/params';
import { StageScreen } from '@/features/workspace/StageScreen';
import { MESSAGES } from '@/shared/messages';

/**
 * One stage of one book, `/projects/<id>/stages/<stage>?page=&scan=&source=&view=&compare=&filter=`, and with a step
 * open `/projects/<id>/stages/<stage>/steps/<step>?...`.
 *
 * The stage is a segment of the path, the step a child segment, and the rest of the view is in the search params, so any
 * view can be linked and reloaded. The child routes draw nothing, so moving between steps leaves this screen mounted, and
 * the child route of the address with no step sends a stage with a step bar to the step it opens on. A segment that names
 * no stage answers with the not-found screen inside the layout of the book, which keeps the header and the stage bar on
 * screen. The screen keeps the place of the reader in the book as they move, so the book opens here again.
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
  const known = parseStage(stage);
  return known === null ? (
    <StageNotFound />
  ) : (
    <KnownStageOfBook projectId={projectId} stage={known} />
  );
}

function KnownStageOfBook({
  projectId,
  stage,
}: {
  projectId: string;
  stage: Stage;
}): React.JSX.Element {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const stepId = parseIdentifier(useParams({ strict: false }).stepId);
  const address = useMemo<PlaceAddress>(
    () => ({ mode: 'workspace', stage, ...search }),
    [stage, search],
  );
  const writer = useBookPlaceWriter(projectId, address);

  // The stage is named in the call, because a move made while the router is already on its way to a screen that has no
  // stage, such as the reading mode that is still loading, would otherwise read the stage from there and go to `undefined`
  const onSearchChange = (changes: Partial<StageSearch>): void => {
    if (stepId === undefined) {
      void navigate({
        params: (previous) => ({ ...previous, stage }),
        search: (previous) => ({ ...previous, ...changes }),
      });
    } else {
      // A change of the page or of the layout keeps the step that is open
      void navigate({
        to: '/projects/$projectId/stages/$stage/steps/$stepId',
        params: (previous) => ({ ...previous, stage, stepId }),
        search: (previous) => ({ ...previous, ...changes }),
      });
    }
  };
  // A step that left the recipe is replaced in the address by another, so Back does not return to the address of a step
  // that is gone; every other move to a step is a step of the history
  const onStepChange = (next: string, replace = false): void => {
    void navigate({
      to: '/projects/$projectId/stages/$stage/steps/$stepId',
      params: (previous) => ({ ...previous, stage, stepId: next }),
      search: (previous) => previous,
      replace,
    });
  };
  let screen = (
    <StageScreen
      projectId={projectId}
      stage={stage}
      search={search}
      stepId={stepId}
      onSearchChange={onSearchChange}
      onStepChange={onStepChange}
    />
  );
  // Import works on files and scans, and the Order stage is a grid of pages with its own panel
  if (stage === 'import') {
    screen = <ImportScreen projectId={projectId} search={search} onSearchChange={onSearchChange} />;
  } else if (stage === 'page-order') {
    screen = <OrderScreen projectId={projectId} search={search} onSearchChange={onSearchChange} />;
  }
  return <PlaceWriterContext value={writer}>{screen}</PlaceWriterContext>;
}
