import { createFileRoute } from '@tanstack/react-router';
import { MESSAGES } from '@/shared/messages';

/**
 * The landing route, a placeholder until the sign-in guard and the book list replace it.
 */

export const Route = createFileRoute('/')({
  component: () => <main className="p-6 text-xl font-semibold">{MESSAGES.app.name}</main>,
});
