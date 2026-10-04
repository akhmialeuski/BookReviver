import { useEffect, useRef } from 'react';
import { isTypingTarget } from '@/features/viewer/keys';

/**
 * Calls a function when the reader presses Ctrl+Z (Cmd+Z on a Mac) while the component that uses it is mounted.
 *
 * A page editor that is open answers the same key itself and stops the event, so this one answers only a key that
 * nothing else took: it listens after the editor does and leaves an event that is already handled alone. A field keeps
 * its own undo, and an open dialog keeps its keys.
 */

const DIALOG_SELECTOR = '[role="dialog"]';

export function useUndoKey(onUndo: () => void): void {
  // The function closes over the state of the screen and changes with every render, so the listener reads the latest
  // through a ref and is added once
  const latest = useRef(onUndo);
  useEffect(() => {
    latest.current = onUndo;
  });

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      const taken =
        (event.ctrlKey || event.metaKey) &&
        !event.shiftKey &&
        !event.altKey &&
        event.key.toLowerCase() === 'z';
      if (
        !taken ||
        event.defaultPrevented ||
        isTypingTarget(event.target) ||
        document.querySelector(DIALOG_SELECTOR) !== null
      ) {
        return;
      }
      event.preventDefault();
      latest.current();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);
}
