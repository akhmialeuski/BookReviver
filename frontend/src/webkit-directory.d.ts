import 'react';

/**
 * Types the non-standard `webkitdirectory` attribute of a file input, which every current browser supports and
 * which turns the file chooser into a folder chooser. React's own typings do not list it.
 */

declare module 'react' {
  interface InputHTMLAttributes<T> extends HTMLAttributes<T> {
    webkitdirectory?: string;
  }
}
