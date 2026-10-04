import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { recipe, step } from '@/features/processing/fixtures';
import { type BarStep, barStepsOf } from '@/features/workspace/steps';
import { useCloseRemovedStep } from '@/features/workspace/useCloseRemovedStep';

/**
 * The step workspace closes when the step the address names is gone from the recipe: not before the step has been seen in
 * it, which is the case of a step just added, and not while the recipe is still read.
 */

const STEPS = barStepsOf(recipe('r', { steps: [step('geometry.deskew', { step_id: 'a' })] }), []);

function Probe({
  stepId,
  open,
  ready,
  onClose,
}: {
  stepId: string | undefined;
  open: BarStep | null;
  ready: boolean;
  onClose: () => void;
}): null {
  useCloseRemovedStep(stepId, open, ready, onClose);
  return null;
}

describe('useCloseRemovedStep', () => {
  let container: HTMLDivElement;
  let root: Root;
  const onClose = vi.fn();

  function render(stepId: string | undefined, open: BarStep | null, ready = true): void {
    act(() => root.render(<Probe stepId={stepId} open={open} ready={ready} onClose={onClose} />));
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    onClose.mockReset();
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  it('closes the step that was in the recipe and is gone from it', () => {
    render('a', STEPS[0] ?? null);
    expect(onClose).not.toHaveBeenCalled();

    render('a', null);

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('leaves alone a step that has not been in the recipe yet, as one just added is not', () => {
    render('new', null);

    expect(onClose).not.toHaveBeenCalled();
  });

  it('closes nothing while the recipe is still read, or when no step is open', () => {
    render('a', STEPS[0] ?? null);
    render('a', null, false);
    render(undefined, null);

    expect(onClose).not.toHaveBeenCalled();
  });

  it('closes a step once, and a step that comes back is seen again', () => {
    render('a', STEPS[0] ?? null);
    render('a', null);
    render('a', null);
    expect(onClose).toHaveBeenCalledTimes(1);

    render('a', STEPS[0] ?? null);
    render('a', null);
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
