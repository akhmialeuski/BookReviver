import { useEffect, useRef } from 'react';
import { isEditableTarget, type KeyPress } from '@/features/viewer/keys';

/**
 * Tells the canvas while Space is held, which shows the picture before the stage for as long as the key is down.
 *
 * Space belongs to a control that has the focus, such as a button, a switch or a slider, and to a dialog that is open,
 * so it is the canvas's only when none of those is the target. The key is stopped from scrolling the page. Losing the
 * focus of the window counts as letting go, so a held key never sticks.
 */

const SPACE_CODE = 'Space';
const DIALOG_SELECTOR = '[role="dialog"]';
const OWN_KEY_CONTROLS = [
  'button',
  'a[href]',
  'summary',
  '[role="button"]',
  '[role="slider"]',
  '[role="switch"]',
  '[role="checkbox"]',
  '[role="radio"]',
  '[role="menuitem"]',
].join(',');

/** A key press with the physical key. */
export interface HoldKeyPress extends KeyPress {
  code: string;
}

/**
 * Tell whether a key press is the key that holds the picture before.
 *
 * @param press The key press.
 * @param dialogOpen Whether a dialog is open, in which case the keys belong to it.
 */
export function isHoldKey(press: HoldKeyPress, dialogOpen: boolean): boolean {
  if (
    dialogOpen ||
    press.code !== SPACE_CODE ||
    press.altKey ||
    press.ctrlKey ||
    press.metaKey ||
    press.shiftKey ||
    isEditableTarget(press.target)
  ) {
    return false;
  }
  return !(press.target instanceof Element && press.target.closest(OWN_KEY_CONTROLS) !== null);
}

/**
 * Call back with true when Space goes down and false when it comes up, while the hook is enabled.
 *
 * @param enabled Whether the key does anything now, which it does only where there is a picture before to show.
 * @param onChange Called with whether the key is held.
 */
export function useHoldKey(enabled: boolean, onChange: (held: boolean) => void): void {
  const latest = useRef(onChange);
  useEffect(() => {
    latest.current = onChange;
  });

  useEffect(() => {
    if (!enabled) {
      return;
    }
    const down = (event: KeyboardEvent): void => {
      if (isHoldKey(event, document.querySelector(DIALOG_SELECTOR) !== null)) {
        event.preventDefault();
        if (!event.repeat) {
          latest.current(true);
        }
      }
    };
    const up = (event: KeyboardEvent): void => {
      if (event.code === SPACE_CODE) {
        latest.current(false);
      }
    };
    const release = (): void => latest.current(false);
    window.addEventListener('keydown', down, { capture: true });
    window.addEventListener('keyup', up, { capture: true });
    window.addEventListener('blur', release);
    return () => {
      window.removeEventListener('keydown', down, { capture: true });
      window.removeEventListener('keyup', up, { capture: true });
      window.removeEventListener('blur', release);
      latest.current(false);
    };
  }, [enabled]);
}
