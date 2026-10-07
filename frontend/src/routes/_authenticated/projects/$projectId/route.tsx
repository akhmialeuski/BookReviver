import { createFileRoute, Outlet } from '@tanstack/react-router';
import { useState } from 'react';
import { useProjectEvents } from '@/features/projects/useProjectEvents';
import { BookHeader } from '@/features/workspace/BookHeader';
import { ShortcutsDialog } from '@/features/workspace/ShortcutsDialog';
import { StageBar } from '@/features/workspace/StageBar';
import { useBookKeys } from '@/features/workspace/useBookKeys';

/**
 * The layout of every screen of one book: the header, the bar of the ten stages and the screen itself under them.
 *
 * It listens to the book's event stream for as long as any screen is open, so the stage bar, the strip and the
 * activity follow imports, runs and page changes without a reload, and it owns the keys that go to a stage or open
 * the shortcuts, so they work on every screen of the book.
 *
 * The screen fills the window and never scrolls as a whole: the frame of the screen under the bar scrolls a screen that
 * is taller than it, such as the page about the book, and the frame is positioned, so nothing inside it can reach below
 * the window and make the document scroll the header and the bar away.
 */

export const Route = createFileRoute('/_authenticated/projects/$projectId')({
  component: BookLayout,
});

function BookLayout(): React.JSX.Element {
  const { projectId } = Route.useParams();
  const navigate = Route.useNavigate();
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  useProjectEvents(projectId);
  useBookKeys((action) => {
    switch (action.kind) {
      case 'stage':
        void navigate({
          to: '/projects/$projectId/stages/$stage',
          params: { projectId, stage: action.stage },
        });
        break;
      case 'about':
        void navigate({ to: '/projects/$projectId/about', params: { projectId } });
        break;
      case 'shortcuts':
        setShortcutsOpen((open) => !open);
        break;
    }
  });

  return (
    <div className="flex h-dvh flex-col">
      <BookHeader projectId={projectId} onShortcuts={() => setShortcutsOpen(true)} />
      <StageBar projectId={projectId} />
      <div className="relative min-h-0 flex-1 overflow-auto" data-testid="book-screen">
        <Outlet />
      </div>
      <ShortcutsDialog open={shortcutsOpen} onOpenChange={setShortcutsOpen} />
    </div>
  );
}
