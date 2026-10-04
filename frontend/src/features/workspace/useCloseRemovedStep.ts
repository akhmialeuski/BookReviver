import { useEffect, useRef } from 'react';
import type { BarStep } from '@/features/workspace/steps';

/**
 * Close the step workspace when the step the address names has been removed from the recipe.
 *
 * A step is told removed only after it was seen in the recipe and is gone from it, so a step that was just added, whose
 * address is set before the recipe has been read again, is not closed. A recipe that is still being read names no step
 * and closes nothing.
 *
 * @param stepId The identifier of the step the address names, or undefined when none is open.
 * @param open The step of the recipe shown that the address names, or null when the recipe has none such.
 * @param ready Whether the recipe shown has been read.
 * @param onClose Called to take the step out of the address.
 */
export function useCloseRemovedStep(
  stepId: string | undefined,
  open: BarStep | null,
  ready: boolean,
  onClose: () => void,
): void {
  const seen = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (stepId === undefined || !ready) {
      return;
    }
    if (open !== null) {
      seen.current = stepId;
    } else if (seen.current === stepId) {
      seen.current = undefined;
      onClose();
    }
  }, [stepId, open, ready, onClose]);
}
