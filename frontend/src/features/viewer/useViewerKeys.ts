import { useEffect, useRef } from 'react';
import { type ViewerKeyAction, viewerKeyAction } from '@/features/viewer/keys';

/**
 * Turns the keys of the viewer into calls while the screen is mounted.
 *
 * The listener sits on the window, in the capture phase, so the keys work without focusing the image first and
 * nothing inside the page gets to scroll or pan on them. A key it acts on is stopped there; every other key goes on.
 */

const DIALOG_SELECTOR = '[role="dialog"]';

export function useViewerKeys(actions: Readonly<Record<ViewerKeyAction, () => void>>): void {
  // The actions close over the page list and change with every render, so the listener reads the latest through a
  // ref and is added once
  const latest = useRef(actions);
  useEffect(() => {
    latest.current = actions;
  });

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      const action = viewerKeyAction(event, document.querySelector(DIALOG_SELECTOR) !== null);
      if (action === null) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      latest.current[action]();
    };
    window.addEventListener('keydown', onKeyDown, { capture: true });
    return () => window.removeEventListener('keydown', onKeyDown, { capture: true });
  }, []);
}
