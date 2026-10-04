import { createFileRoute } from '@tanstack/react-router';

/**
 * One step of one stage of a book, `/projects/<id>/stages/<stage>/steps/<step>`.
 *
 * It is a child of the route of the stage, so the screen of the stage stays mounted as the reader moves from one step to
 * another and keeps its canvas, its strip and its place. The step is only a segment of the address: the stage route reads
 * it and draws the step open, and a step that the recipe of the stage does not have leaves the stage as it is.
 */

export const Route = createFileRoute(
  '/_authenticated/projects/$projectId/stages/$stage/steps/$stepId',
)({});
