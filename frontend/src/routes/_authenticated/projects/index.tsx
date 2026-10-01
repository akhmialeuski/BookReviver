import { createFileRoute } from '@tanstack/react-router';
import { MESSAGES } from '@/shared/messages';

/**
 * The list of the books of the signed-in account, a placeholder until the list itself is built.
 */

export const Route = createFileRoute('/_authenticated/projects/')({
  component: () => <h1 className="text-2xl font-semibold">{MESSAGES.projects.title}</h1>,
});
