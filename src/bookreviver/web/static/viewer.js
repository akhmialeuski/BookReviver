// Page viewer keyboard navigation: arrow keys, Home and End follow the pager links marked with data-hotkey.
// The links work on their own, so the page stays usable without JavaScript.
'use strict';

const EDITABLE_SELECTOR = 'input, textarea, select, [contenteditable]';

document.addEventListener('keydown', (event) => {
  if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) {
    return;
  }
  if (event.target instanceof Element && event.target.closest(EDITABLE_SELECTOR)) {
    return;
  }
  const link = document.querySelector(`a[data-hotkey="${CSS.escape(event.key)}"]`);
  if (link instanceof HTMLAnchorElement) {
    event.preventDefault();
    window.location.assign(link.href);
  }
});
