import { cn } from '@/shared/lib/utils';

/**
 * The one heading of a section in the panel of a stage: small, bold, upper case and grey.
 *
 * Every section the panel draws, and every block inside a slot that needs a name of its own, uses this heading, so the
 * look of a heading changes in one place. The title of the open step is not a section heading and is drawn by the layout.
 */

export function PanelHeading({
  className,
  ...props
}: React.ComponentProps<'h3'>): React.JSX.Element {
  return (
    <h3
      className={cn(
        'text-xs font-semibold tracking-wide text-muted-foreground uppercase',
        className,
      )}
      {...props}
    />
  );
}
