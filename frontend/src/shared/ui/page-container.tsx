import { cn } from '@/shared/lib/utils';

/**
 * The centred column most screens of the signed-in area sit in. A screen that needs the whole width, such as the
 * viewer, does not use it.
 */

export function PageContainer({
  className,
  ...props
}: React.ComponentProps<'main'>): React.JSX.Element {
  return <main className={cn('mx-auto w-full max-w-5xl px-4 py-8', className)} {...props} />;
}
