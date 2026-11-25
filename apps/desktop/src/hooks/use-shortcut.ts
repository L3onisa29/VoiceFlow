import { useEffect, useRef } from "react";

/**
 * In-app keyboard shortcut, e.g. `useShortcut("mod+k", open)`.
 * `mod` is ⌘ on macOS; `shift` and `alt` are optional modifiers. Shortcuts are
 * ignored while typing in inputs, textareas, selects or contenteditable
 * elements unless `allowInInputs` is set. Page-level shortcuts (⌘F, ⌘N…) use
 * the same hook from the page component, so they only exist while it is mounted.
 */
export function useShortcut(
  shortcut: string,
  handler: (event: KeyboardEvent) => void,
  { enabled = true, allowInInputs = false } = {},
) {
  const latest = useRef(handler);
  useEffect(() => {
    latest.current = handler;
  });
  useEffect(() => {
    if (!enabled) return;
    const parts = shortcut.toLowerCase().split("+");
    const key = parts.pop()!;
    const wanted = {
      meta: parts.includes("mod"),
      shift: parts.includes("shift"),
      alt: parts.includes("alt"),
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing || event.repeat) return;
      if (
        event.metaKey !== wanted.meta ||
        event.shiftKey !== wanted.shift ||
        event.altKey !== wanted.alt ||
        event.ctrlKey
      )
        return;
      if (event.key.toLowerCase() !== key) return;
      if (!allowInInputs && isTypingTarget(event.target)) return;
      event.preventDefault();
      latest.current(event);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [shortcut, enabled, allowInInputs]);
}

export function isTypingTarget(target: EventTarget | null) {
  return (
    target instanceof HTMLElement &&
    (target.isContentEditable ||
      target.closest(
        'input, textarea, select, [contenteditable]:not([contenteditable="false"])',
      ) !== null)
  );
}
