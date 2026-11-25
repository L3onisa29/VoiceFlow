import { useCallback, useEffect, useLayoutEffect, useRef } from "react";
import { partsBySentence, type DiffPart } from "@/lib/corrections";
import { cn } from "@/lib/utils";

const escape = (text: string) =>
  text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");

// Classes are written out in full so Tailwind finds them in this file.
export const ADDED =
  "rounded-[3px] bg-emerald-500/20 text-emerald-700 dark:text-emerald-300";
export const REMOVED =
  "rounded-[3px] bg-red-500/15 text-red-700 line-through dark:text-red-300";
const TRACE =
  "before:rounded-[3px] before:bg-red-500/15 before:px-px before:text-red-700 before:line-through before:content-[attr(data-t)] dark:before:text-red-300";
const SENTENCE =
  "rounded-sm transition-colors [box-decoration-break:clone] [-webkit-box-decoration-break:clone]";
const BAND = "bg-foreground/[0.06]";
/** The sentence holding the caret. Toggled without re-rendering the text. */
const ACTIVE = [
  "bg-primary/15",
  "shadow-[inset_0_-1.5px_0_var(--color-primary)]",
];

function mark(kind: "add" | "rem", value: string) {
  const [, lead, body, trail] = value.match(/^(\s*)([\s\S]*?)(\s*)$/) ?? [];
  return body
    ? `${escape(lead)}<mark data-diff="${kind}" class="${kind === "add" ? ADDED : REMOVED}">${escape(body)}</mark>${escape(trail)}`
    : escape(value);
}

/** A removal is drawn from data-t by a pseudo-element: never part of the editable text. */
function removal(value: string) {
  if (!value.trim())
    return `<span data-diff="del" data-space="true" contenteditable="false" data-t="·" title="Spazio rimosso" class="${TRACE} before:px-0.5 before:font-bold before:no-underline"></span>`;
  // The removed words took a space with them: draw it back around the trace.
  const spacing = `${/^\s/.test(value) ? " before:ml-1" : ""}${/\s$/.test(value) ? " before:mr-1" : ""}`;
  return `<span data-diff="del" contenteditable="false" data-t="${escape(value.trim())}" class="${TRACE}${spacing}"></span>`;
}

function sentenceHtml(parts: DiffPart[], compare: boolean) {
  return parts
    .map(([kind, value], k) => {
      if (kind === "=") return escape(value);
      if (kind === "+" || kind === "~") return mark("add", value);
      if (compare) return mark("rem", value);
      // A replacement shows only the new text; a pure removal leaves its trace.
      const replaced = parts[k - 1]?.[0] === "+" || parts[k + 1]?.[0] === "+";
      return replaced ? "" : removal(value);
    })
    .join("");
}

function render(
  original: string,
  value: string,
  compare: boolean,
  groups: boolean,
) {
  return partsBySentence(original, value)
    .map(
      (parts, k) =>
        `<span data-k="${k}" class="${SENTENCE}${groups && k % 2 ? ` ${BAND}` : ""}">${sentenceHtml(parts, compare)}</span>`,
    )
    .join("");
}

type Selection = [number, number];

function offsetIn(root: HTMLElement, node: Node, offset: number) {
  const range = document.createRange();
  range.selectNodeContents(root);
  range.setEnd(node, offset);
  return range.toString().length;
}

function readSelection(root: HTMLElement): Selection | null {
  const selection = window.getSelection();
  if (
    !selection?.rangeCount ||
    !selection.anchorNode ||
    !selection.focusNode ||
    !root.contains(selection.anchorNode)
  )
    return null;
  return [
    offsetIn(root, selection.anchorNode, selection.anchorOffset),
    offsetIn(root, selection.focusNode, selection.focusOffset),
  ];
}

function pointAt(root: HTMLElement, position: number): [Node, number] {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let last: Text | null = null;
  let rest = position;
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node as Text;
    if (rest <= text.length) return [text, rest];
    rest -= text.length;
    last = text;
  }
  return last ? [last, last.length] : [root, 0];
}

function writeSelection(root: HTMLElement, [anchor, focus]: Selection) {
  const [anchorNode, anchorOffset] = pointAt(root, anchor);
  const [focusNode, focusOffset] = pointAt(root, focus);
  window
    .getSelection()
    ?.setBaseAndExtent(anchorNode, anchorOffset, focusNode, focusOffset);
}

const plain = (root: HTMLElement) =>
  (root.textContent ?? "").replace(/ /g, " ");

/**
 * Free text editing with the corrections drawn in place: added characters in green,
 * removed ones as a red trace that is not part of the text. Sentences of the original
 * are shown as bands; they guide, they never constrain where the user writes.
 */
export function CorrectionEditor({
  original,
  value,
  onChange,
  compare = false,
  groups = true,
  readOnly = false,
  label,
  className,
  onActiveSentence,
  onEscape,
}: {
  original: string;
  value: string;
  onChange: (next: string) => void;
  compare?: boolean;
  groups?: boolean;
  readOnly?: boolean;
  label: string;
  className?: string;
  onActiveSentence?: (sentence: number | null) => void;
  onEscape?: () => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const html = useRef("");
  const pending = useRef<Selection | null>(null);
  const composing = useRef(false);
  const undo = useRef<{ value: string; selection: Selection | null }[]>([]);
  const redo = useRef<{ value: string; selection: Selection | null }[]>([]);
  const lastPush = useRef(0);
  const editable = !readOnly && !compare;

  useLayoutEffect(() => {
    const element = root.current;
    if (!element) return;
    const next = render(original, value, compare, groups);
    if (next === html.current) return;
    const focused = document.activeElement === element;
    const selection =
      pending.current ?? (focused ? readSelection(element) : null);
    pending.current = null;
    html.current = next;
    element.innerHTML = next;
    if (focused && selection) writeSelection(element, selection);
  }, [original, value, compare, groups]);

  const markActive = useCallback(() => {
    const element = root.current;
    if (!element) return;
    let active: number | null = null;
    const selection = window.getSelection();
    if (document.activeElement === element && selection?.focusNode) {
      let node: Node | null = selection.focusNode;
      while (node && node !== element) {
        if (node instanceof HTMLElement && node.dataset.k !== undefined) {
          active = Number(node.dataset.k);
          break;
        }
        node = node.parentNode;
      }
    }
    for (const sentence of element.querySelectorAll<HTMLElement>("[data-k]"))
      for (const name of ACTIVE)
        sentence.classList.toggle(
          name,
          groups && Number(sentence.dataset.k) === active,
        );
    onActiveSentence?.(active);
  }, [onActiveSentence, groups]);

  useEffect(() => {
    document.addEventListener("selectionchange", markActive);
    return () => document.removeEventListener("selectionchange", markActive);
  }, [markActive]);

  const commit = (next: string, selection: Selection | null) => {
    pending.current = selection;
    onChange(next);
  };

  const onInput = () => {
    const element = root.current;
    if (!element || composing.current) return;
    const next = plain(element);
    if (next === value) return;
    const now = Date.now();
    if (now - lastPush.current > 700)
      undo.current.push({ value, selection: readSelection(element) });
    if (undo.current.length > 200) undo.current.shift();
    redo.current = [];
    lastPush.current = now;
    commit(next, readSelection(element));
  };

  const travel = (from: typeof undo.current, to: typeof undo.current) => {
    const element = root.current;
    const state = from.pop();
    if (!element || !state) return;
    to.push({ value, selection: readSelection(element) });
    lastPush.current = 0;
    commit(state.value, state.selection);
  };

  return (
    <div
      ref={root}
      role="textbox"
      aria-label={label}
      aria-multiline="true"
      aria-readonly={!editable}
      contentEditable={editable}
      suppressContentEditableWarning
      spellCheck={false}
      className={cn(
        "whitespace-pre-wrap wrap-anywhere caret-primary outline-none select-text",
        className,
      )}
      onInput={onInput}
      onCompositionStart={() => {
        composing.current = true;
      }}
      onCompositionEnd={() => {
        composing.current = false;
        onInput();
      }}
      onBeforeInput={(event) => {
        const type = (event.nativeEvent as InputEvent).inputType;
        if (type === "insertParagraph" || type === "insertLineBreak") {
          event.preventDefault();
          document.execCommand("insertText", false, "\n");
        }
      }}
      onPaste={(event) => {
        event.preventDefault();
        document.execCommand(
          "insertText",
          false,
          event.clipboardData.getData("text/plain"),
        );
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          // The first Escape leaves the text; the page handles the next one.
          event.preventDefault();
          event.stopPropagation();
          root.current?.blur();
          onEscape?.();
          return;
        }
        if (
          !(event.metaKey || event.ctrlKey) ||
          event.key.toLowerCase() !== "z"
        )
          return;
        event.preventDefault();
        if (event.shiftKey) travel(redo.current, undo.current);
        else travel(undo.current, redo.current);
      }}
      onBlur={markActive}
    />
  );
}
