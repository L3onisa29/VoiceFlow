/**
 * Corrections of a dictation, compared with the text VoiceFlow produced.
 *
 * Words are aligned first, so a longer or inserted word never shifts the rest of the
 * text; a replaced word that is still similar is refined to the single characters that
 * changed. Sentences of the produced text group the changes: each changed sentence is
 * saved as its own revision, with its own category.
 */

/** `=` unchanged, `~` same letter with another case, `+` added, `-` removed. */
export type DiffKind = "=" | "~" | "+" | "-";
export type DiffPart = [DiffKind, string];
/** Categories the backend remembers. `null` is a content change: it teaches nothing. */
export type Lesson = "recognition" | "cleanup" | "style";

type Op = ["=" | "+" | "-", number];

/** Longest common subsequence, trimmed to the changed middle first: edits are local. */
function align<T>(a: T[], b: T[], same: (x: T, y: T) => boolean): Op[] {
  let start = 0;
  while (start < a.length && start < b.length && same(a[start], b[start]))
    start++;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && same(a[endA - 1], b[endB - 1])) {
    endA--;
    endB--;
  }
  const n = endA - start;
  const m = endB - start;
  const ops: Op[] = [];
  for (let k = 0; k < start; k++) ops.push(["=", k]);
  if (n > 0 && m > 0) {
    const width = m + 1;
    const table = new Uint32Array((n + 1) * width);
    for (let i = n - 1; i >= 0; i--)
      for (let j = m - 1; j >= 0; j--)
        table[i * width + j] = same(a[start + i], b[start + j])
          ? table[(i + 1) * width + j + 1] + 1
          : Math.max(table[(i + 1) * width + j], table[i * width + j + 1]);
    let i = 0;
    let j = 0;
    while (i < n && j < m) {
      if (same(a[start + i], b[start + j])) {
        ops.push(["=", start + i]);
        i++;
        j++;
      } else if (table[(i + 1) * width + j] >= table[i * width + j + 1])
        ops.push(["-", start + i++]);
      else ops.push(["+", start + j++]);
    }
    while (i < n) ops.push(["-", start + i++]);
    while (j < m) ops.push(["+", start + j++]);
  } else {
    for (let i = 0; i < n; i++) ops.push(["-", start + i]);
    for (let j = 0; j < m; j++) ops.push(["+", start + j]);
  }
  for (let k = 0; k < a.length - endA; k++) ops.push(["=", endA + k]);
  return ops;
}

const TOKEN = /\s+|[\p{L}\p{N}'’]+|[^\s\p{L}\p{N}]/gu;
const tokens = (text: string) => text.match(TOKEN) ?? [];

function similarity(a: string, b: string) {
  const x = [...a.toLowerCase()];
  const y = [...b.toLowerCase()];
  const common = align(x, y, (p, q) => p === q).filter(
    ([kind]) => kind === "=",
  ).length;
  return common / Math.max(x.length, y.length);
}

export function diffParts(before: string, after: string): DiffPart[] {
  const a = tokens(before);
  const b = tokens(after);
  const out: DiffPart[] = [];
  const push = (kind: DiffKind, value: string) => {
    if (!value) return;
    const last = out[out.length - 1];
    if (last && last[0] === kind) last[1] += value;
    else out.push([kind, value]);
  };
  const ops = align(a, b, (x, y) => x === y);
  let i = 0;
  let j = 0;
  for (let k = 0; k < ops.length;) {
    if (ops[k][0] === "=") {
      push("=", a[i]);
      i++;
      j++;
      k++;
      continue;
    }
    let removed = "";
    let added = "";
    while (k < ops.length && ops[k][0] !== "=") {
      if (ops[k][0] === "-") removed += a[i++];
      else added += b[j++];
      k++;
    }
    if (removed && added && similarity(removed, added) >= 0.5) {
      const x = [...removed];
      const y = [...added];
      let p = 0;
      let q = 0;
      for (const [kind] of align(
        x.map((c) => c.toLowerCase()),
        y.map((c) => c.toLowerCase()),
        (c, d) => c === d,
      )) {
        if (kind === "=") {
          const original = x[p++];
          const changed = y[q++];
          push(original === changed ? "=" : "~", changed);
        } else if (kind === "-") push("-", x[p++]);
        else push("+", y[q++]);
      }
    } else {
      push("-", removed);
      push("+", added);
    }
  }
  return out;
}

const BOUNDARY = /[.!?…]+["»”')\]]*\s+|\n+/g;

/** Where each sentence of `text` starts. The space after a sentence belongs to it. */
export function sentenceStarts(text: string): number[] {
  const starts = [0];
  for (const match of text.matchAll(BOUNDARY)) {
    const end = (match.index ?? 0) + match[0].length;
    if (end < text.length) starts.push(end);
  }
  return starts;
}

/**
 * The diff split by the sentences of `original`. Text added where a sentence starts
 * belongs to that sentence; text added at the end of one belongs to it as well.
 * Joining every part except removals gives back `current` exactly.
 */
export function partsBySentence(original: string, current: string) {
  const starts = sentenceStarts(original);
  const sentenceAt = (offset: number) => {
    let k = 0;
    while (k + 1 < starts.length && starts[k + 1] <= offset) k++;
    return k;
  };
  const sentences: DiffPart[][] = starts.map(() => []);
  let offset = 0;
  for (const [kind, value] of diffParts(original, current)) {
    if (kind === "+") {
      sentences[sentenceAt(offset)].push([kind, value]);
      continue;
    }
    let rest = value;
    while (rest) {
      const k = sentenceAt(offset);
      const end = k + 1 < starts.length ? starts[k + 1] : Infinity;
      const take = Math.min(rest.length, end - offset);
      sentences[k].push([kind, rest.slice(0, take)]);
      offset += take;
      rest = rest.slice(take);
    }
  }
  return sentences;
}

/** `current` cut along the sentences of `original`. */
export function sentenceTexts(original: string, current: string) {
  return partsBySentence(original, current).map((parts) =>
    parts
      .filter(([kind]) => kind !== "-")
      .map(([, value]) => value)
      .join(""),
  );
}

export function originalSentences(original: string) {
  const starts = sentenceStarts(original);
  return starts.map((start, k) => original.slice(start, starts[k + 1]));
}

const FILLERS = new Set([
  "ehm",
  "eh",
  "uhm",
  "um",
  "uh",
  "mmh",
  "mh",
  "cioè",
  "tipo",
  "allora",
  "insomma",
]);
const WORD = /[\p{L}\p{N}'’]/u;
const isWord = (char: string | undefined) => !!char && WORD.test(char);
const words = (text: string) =>
  text
    .toLowerCase()
    .split(/[^\p{L}\p{N}'’]+/u)
    .filter(Boolean);

type Change = "style" | "cleanup" | "content" | "recognition";

/**
 * One group of adjacent changes. Letters that only changed case, punctuation and
 * spacing between words are style; a word split, joined or rewritten is
 * recognition; a repeated or filler word removed is cleanup; other words added or
 * removed are content.
 */
function classify(parts: DiffPart[], from: number, to: number): Change {
  let removed = "";
  let added = "";
  for (let k = from; k < to; k++) {
    if (parts[k][0] === "-") removed += parts[k][1];
    if (parts[k][0] === "+") added += parts[k][1];
  }
  const before = parts[from - 1]?.[1] ?? "";
  const after = parts[to]?.[1] ?? "";
  const previous = before[before.length - 1];
  const next = after[0];
  const changed = removed + added;
  if (!changed) return "style";
  if (!/[\p{L}\p{N}]/u.test(changed)) {
    // Space added or dropped between two letters splits or joins a word.
    return /\s/u.test(changed) && isWord(previous) && isWord(next)
      ? "recognition"
      : "style";
  }
  const touchesWord =
    (isWord(previous) && isWord(changed[0])) ||
    (isWord(next) && isWord(changed[changed.length - 1]));
  if (touchesWord || (removed.trim() && added.trim())) return "recognition";
  if (added) return "content";
  const earlier = words(before);
  const neighbours = new Set([earlier[earlier.length - 1], words(after)[0]]);
  return words(removed).every(
    (item) => FILLERS.has(item) || neighbours.has(item),
  )
    ? "cleanup"
    : "content";
}

/** What a change most likely corrects. `null` means the content itself changed. */
export function lessonFor(before: string, after: string): Lesson | null {
  if (before === after) return null;
  const parts = diffParts(before, after);
  const found = new Set<Change>();
  for (let k = 0; k < parts.length;) {
    if (parts[k][0] === "=") {
      k++;
      continue;
    }
    let end = k;
    while (end < parts.length && parts[end][0] !== "=") end++;
    found.add(classify(parts, k, end));
    k = end;
  }
  // The most informative lesson wins: a recognition fix is worth teaching even
  // when the sentence was also tidied in the same edit.
  if (found.has("recognition")) return "recognition";
  if (found.has("content")) return null;
  if (found.has("cleanup")) return "cleanup";
  return found.has("style") ? "style" : null;
}

export type Step = {
  segment: number;
  text: string;
  category: Lesson | null;
  restore: boolean;
};

/**
 * One cumulative step per sentence that differs between `saved` and `draft`, in
 * sentence order. Every step's text is the whole text after it, so each revision has a
 * complete before/after pair. A sentence brought back to the produced text is a restore.
 */
export function buildSteps(
  produced: string,
  saved: string,
  draft: string,
  lesson: (segment: number, before: string, after: string) => Lesson | null,
): Step[] {
  const original = originalSentences(produced);
  const current = sentenceTexts(produced, saved);
  const wanted = sentenceTexts(produced, draft);
  const steps: Step[] = [];
  for (let k = 0; k < wanted.length; k++) {
    if (wanted[k] === current[k]) continue;
    current[k] = wanted[k];
    const restore = wanted[k] === original[k];
    steps.push({
      segment: k,
      text: current.join(""),
      category: restore ? null : lesson(k, original[k], wanted[k]),
      restore,
    });
  }
  return steps;
}

/** A sentence that differs from the produced text, for lists of corrections. */
export type SentenceChange = {
  segment: number;
  before: string;
  after: string;
  parts: DiffPart[];
};

export function sentenceChanges(
  produced: string,
  current: string,
): SentenceChange[] {
  const original = originalSentences(produced);
  return partsBySentence(produced, current).flatMap((parts, segment) => {
    const after = parts
      .filter(([kind]) => kind !== "-")
      .map(([, value]) => value)
      .join("");
    return after === original[segment]
      ? []
      : [{ segment, before: original[segment], after, parts }];
  });
}
