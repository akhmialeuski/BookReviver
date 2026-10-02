import { useEffect, useRef } from 'react';
import { type BookKeyAction, bookKeyAction } from '@/features/workspace/keys';

/**
 * Turns the keys of a book screen into calls while the layout of the book is mounted.
 *
 * Like the viewer's keys it listens on the window in the capture phase, and it stops only a key it acts on, so the
 * page keys of the viewer and the field a reader types in are left alone.
 */

const DIALOG_SELECTOR = '[role="dialog"]';

export function useBookKeys(onAction: (action: BookKeyAction) => void): void {
  // The handler closes over the router and the dialog state and changes with every render, so the listener reads the
  // latest through a ref and is added once
  const latest = useRef(onAction);
  useEffect(() => {
    latest.current = onAction;
  });

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      const action = bookKeyAction(event, document.querySelector(DIALOG_SELECTOR) !== null);
      if (action === null) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      latest.current(action);
    };
    window.addEventListener('keydown', onKeyDown, { capture: true });
    return () => window.removeEventListener('keydown', onKeyDown, { capture: true });
  }, []);
}
