import { CircleAlertIcon } from 'lucide-react';
import { Alert, AlertDescription } from '@/shared/ui/alert';

/**
 * The red box every form and panel shows when something failed, with a message already written for a person.
 */

export function ErrorAlert({ message }: { message: string }): React.JSX.Element {
  return (
    <Alert variant="destructive">
      <CircleAlertIcon />
      <AlertDescription>{message}</AlertDescription>
    </Alert>
  );
}
