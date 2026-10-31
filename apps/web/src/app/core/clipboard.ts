// Clipboard helper: copies text with the asynchronous Clipboard API or, failing that, the old command.

/**
 * Puts `text` on the clipboard and resolves to whether it worked; it never rejects.
 *
 * The asynchronous API is tried first. It is missing outside a secure context and the desktop
 * window denies its permission, so a hidden text area and `document.execCommand('copy')` serve as
 * the fallback. Call it from a click handler: browsers refuse a copy without a user gesture.
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Fall through to the command; the permission may be denied while the command still works.
  }
  return copyWithCommand(text);
}

/** Copies through a temporary, off-screen text area; false when the browser has no such command. */
function copyWithCommand(text: string): boolean {
  if (typeof document === 'undefined' || typeof document.execCommand !== 'function') {
    return false;
  }
  const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  area.setAttribute('aria-hidden', 'true');
  area.style.position = 'fixed';
  area.style.top = '0';
  area.style.left = '-9999px';
  document.body.appendChild(area);
  try {
    area.select();
    return document.execCommand('copy');
  } catch {
    return false;
  } finally {
    area.remove();
    previous?.focus();
  }
}
