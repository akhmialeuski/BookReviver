import { MESSAGES } from '@/shared/messages';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/shared/ui/card';

/**
 * The frame of the pages for visitors: the application name above a centred card with a title and a description.
 */

export function AuthCard({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <main className="mx-auto flex min-h-screen w-full max-w-md flex-col justify-center gap-6 p-6">
      <p className="text-center text-2xl font-semibold tracking-tight">{MESSAGES.app.name}</p>
      <Card>
        <CardHeader>
          <CardTitle className="text-xl">{title}</CardTitle>
          {description === undefined ? null : <CardDescription>{description}</CardDescription>}
        </CardHeader>
        <CardContent className="grid gap-4">{children}</CardContent>
      </Card>
    </main>
  );
}
