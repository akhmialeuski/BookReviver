import { createFileRoute } from '@tanstack/react-router';
import { redirectToDefaultStep } from '@/features/workspace/defaultStep';

/**
 * A stage of one book with no step in the address, `/projects/<id>/stages/<stage>?...`.
 *
 * A stage with a step bar always has a step open, so its loader sends this address to the step the stage opens on, with the
 * search params kept and in place of the address, which Back does not return to. The redirect belongs to the router and
 * not to the screen, so a navigation started while the data is still being read, such as a click on the reading mode,
 * cancels it and the address that was clicked is not overwritten. Any other stage draws no step and stays here.
 */

export const Route = createFileRoute('/_authenticated/projects/$projectId/stages/$stage/')({
  // The open page decides the recipe the step is chosen in, as on the screen
  loaderDeps: ({ search }) => ({ page: search.page }),
  loader: ({ context, params, deps }) =>
    redirectToDefaultStep(context.queryClient, params, deps.page),
});
