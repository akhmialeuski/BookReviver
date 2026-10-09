import type { LibraryBook } from '@/features/profiles/ProfileCard';
import { ProfileLibrary } from '@/features/profiles/ProfileLibrary';
import { MESSAGES } from '@/shared/messages';
import { Sheet, SheetContent, SheetTitle } from '@/shared/ui/sheet';

/**
 * The side panel that holds the library of profiles while a book is open. The profile menu opens it, and it is closed
 * with its button or the Escape key.
 */

const labels = MESSAGES.profiles.library;

export function ProfileLibraryPanel({
  open,
  onOpenChange,
  book,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  book: LibraryBook;
}): React.JSX.Element {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="gap-3 overflow-y-auto p-4 sm:max-w-md"
        aria-describedby={undefined}
        data-testid="profile-library-panel"
      >
        <SheetTitle>{labels.title}</SheetTitle>
        <ProfileLibrary book={book} />
      </SheetContent>
    </Sheet>
  );
}
