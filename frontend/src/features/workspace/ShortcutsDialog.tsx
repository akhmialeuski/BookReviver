import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/ui/dialog';

/**
 * The sheet of keyboard shortcuts of a book screen, opened by the question mark.
 *
 * It lists the keys that work now in three columns, the pages, the stages and the screen itself, and its texts are in
 * `MESSAGES.shortcuts`. A key is drawn as a keycap, and a combination as several of them in a row.
 */

function Keycap({ children }: { children: string }): React.JSX.Element {
  return (
    <kbd className="inline-flex min-w-6 items-center justify-center rounded border bg-muted px-1.5 py-0.5 font-sans text-xs text-muted-foreground">
      {children}
    </kbd>
  );
}

export function ShortcutsDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}): React.JSX.Element {
  const labels = MESSAGES.shortcuts;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{labels.title}</DialogTitle>
          <DialogDescription>{labels.hint}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-6 sm:grid-cols-3">
          {labels.groups.map((group) => (
            <section key={group.title} className="grid content-start gap-2">
              <h3 className="text-xs font-medium tracking-wider text-muted-foreground uppercase">
                {group.title}
              </h3>
              <ul className="grid gap-2">
                {group.items.map((item) => (
                  <li key={item.label} className="flex items-center justify-between gap-3 text-sm">
                    <span>{item.label}</span>
                    <span className="flex shrink-0 gap-1">
                      {item.keys.map((key) => (
                        <Keycap key={key}>{key}</Keycap>
                      ))}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">{MESSAGES.common.close}</Button>
          </DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
