import { createFileRoute, redirect } from '@tanstack/react-router';
import { DEFAULT_LANDING } from '@/features/auth/redirect';

/**
 * The root address has no screen of its own: it leads to the list of books, and the guard of that area sends a
 * visitor on to the sign-in page.
 */

export const Route = createFileRoute('/')({
  beforeLoad: () => {
    throw redirect({ href: DEFAULT_LANDING });
  },
});
