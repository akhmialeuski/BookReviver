import { createFileRoute, redirect } from '@tanstack/react-router';
import type { Stage } from '@/api';
import { projectApiV1ProjectsProjectIdGetOptions } from '@/api/@tanstack/react-query.gen';
import { startStage } from '@/features/stages/parse';
import { recallStage } from '@/features/workspace/storage';

/**
 * The address of one book, `/projects/<id>`, which shows nothing of its own and sends the reader to the stage the
 * book was left on.
 *
 * The stage last opened for the book in this browser wins. Without such a record the book opens on the `next_stage`
 * of its summary, and a book with neither opens on the first stage.
 */

export const Route = createFileRoute('/_authenticated/projects/$projectId/')({
  beforeLoad: async ({ context, params }) => {
    const remembered = recallStage(params.projectId);
    let next: Stage | null = null;
    if (remembered === null) {
      try {
        const project = await context.queryClient.fetchQuery(
          projectApiV1ProjectsProjectIdGetOptions({ path: { project_id: params.projectId } }),
        );
        next = project.next_stage;
      } catch {
        // A missing book or a failed request shows its error on the stage screen, which asks for the book again
      }
    }
    throw redirect({
      to: '/projects/$projectId/stages/$stage',
      params: { projectId: params.projectId, stage: startStage(remembered, next) },
      replace: true,
    });
  },
});
