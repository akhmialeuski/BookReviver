import { createFileRoute } from '@tanstack/react-router';
import { ForgotPasswordForm } from '@/features/auth/ForgotPasswordForm';

/**
 * The page that asks for a password reset mail, reached from the sign-in screen.
 */

export const Route = createFileRoute('/forgot-password')({
  component: ForgotPasswordForm,
});
