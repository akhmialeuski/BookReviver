import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

/**
 * Join class names into one string, and let the last of two conflicting Tailwind utilities win.
 *
 * `clsx` drops falsy values and flattens arrays and objects, and `twMerge` removes a utility that a later one
 * overrides, so a component can accept a `className` that adjusts its own defaults without both applying.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
