import { ProfileLibrary } from '@/features/profiles/ProfileLibrary';
import { MESSAGES } from '@/shared/messages';

/**
 * The recipe profiles of the signed-in account outside a book: the same library the side panel of a book holds, without
 * the actions that need a book. The account menu and the bar of the library link here.
 */

const labels = MESSAGES.profiles.page;

export function ProfilesPage(): React.JSX.Element {
  return (
    <div className="grid gap-6">
      <div className="grid gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">{labels.title}</h1>
        <p className="text-sm text-muted-foreground">{labels.description}</p>
      </div>
      <ProfileLibrary />
    </div>
  );
}
