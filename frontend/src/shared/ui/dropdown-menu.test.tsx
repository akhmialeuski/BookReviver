import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';

/**
 * The dropdown menu around Radix: a press on the trigger of another menu still dismisses an open menu, while a
 * press on its own trigger during its fade-out opens it again.
 */

describe('DropdownMenu', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  const list = (name: string): Element | null =>
    document.querySelector(`[data-testid="list-${name}"]`);

  async function press(target: Element | null): Promise<void> {
    await act(async () => {
      target?.dispatchEvent(
        new MouseEvent('pointerdown', { bubbles: true, cancelable: true, button: 0 }),
      );
      await new Promise((resolve) => setTimeout(resolve));
    });
  }

  async function renderTwoMenus(): Promise<void> {
    await act(async () => {
      root.render(
        <>
          {['a', 'b'].map((name) => (
            <DropdownMenu key={name}>
              <DropdownMenuTrigger data-testid={`trigger-${name}`}>{name}</DropdownMenuTrigger>
              <DropdownMenuContent data-testid={`list-${name}`}>
                <DropdownMenuItem>Pick</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          ))}
        </>,
      );
    });
  }

  it('closes an open menu when the trigger of another menu is pressed', async () => {
    await renderTwoMenus();
    const triggerOf = (name: string): Element | null =>
      container.querySelector(`[data-testid="trigger-${name}"]`);
    await press(triggerOf('a'));
    expect(list('a')?.getAttribute('data-state')).toBe('open');

    await press(triggerOf('b'));

    expect(list('a')).toBeNull();
  });
});
