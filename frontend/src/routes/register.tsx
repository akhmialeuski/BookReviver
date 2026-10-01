import { createFileRoute } from '@tanstack/react-router';
import { RegisterForm } from '@/features/auth/RegisterForm';

/**
 * The registration page.
 */

export const Route = createFileRoute('/register')({
  component: RegisterForm,
});
