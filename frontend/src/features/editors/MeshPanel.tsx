import { Grid3x3Icon } from 'lucide-react';
import { setMoreControl, useMoreControl } from '@/features/editors/moreControl';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';

/** The part of the curves editor in the panel: the "More control" switch. */
export function MeshPanel(): React.JSX.Element {
  const showAll = useMoreControl();
  return (
    <Button
      variant={showAll ? 'secondary' : 'outline'}
      size="sm"
      aria-pressed={showAll}
      data-testid="mesh-more-control"
      onClick={() => setMoreControl(!showAll)}
    >
      <Grid3x3Icon />
      {showAll ? MESSAGES.editors.mesh.fewerControls : MESSAGES.editors.mesh.moreControl}
    </Button>
  );
}
