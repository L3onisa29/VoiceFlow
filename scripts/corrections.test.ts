import { test } from "node:test";
import assert from "node:assert/strict";
import {
  buildSteps,
  diffParts,
  lessonFor,
  partsBySentence,
  sentenceChanges,
  sentenceStarts,
  sentenceTexts,
} from "../apps/desktop/src/lib/corrections.ts";

const changed = (before: string, after: string) =>
  diffParts(before, after).filter(([kind]) => kind !== "=");

test("a longer or inserted word never marks the following text as changed", () => {
  assert.deepEqual(
    changed(
      "la revisione dei suggerimenti del dizionario",
      "la revisione approfondita dei suggerimenti del dizionario",
    ),
    [["+", "approfondita "]],
  );
  assert.deepEqual(changed("una call giovedì", "una chiamata giovedì"), [
    ["-", "call"],
    ["+", "chiamata"],
  ]);
  assert.deepEqual(changed("gira bene", "girava bene"), [["+", "va"]]);
});

test("similar words are refined to the characters that changed, case included", () => {
  assert.deepEqual(changed("progetto voice flow.", "progetto VoiceFlow."), [
    ["~", "V"],
    ["-", " "],
    ["~", "F"],
  ]);
  assert.deepEqual(changed("con tauri la", "con Tauri la"), [["~", "T"]]);
});

test("deletions are exact, repetitions and punctuation stay separate tokens", () => {
  assert.deepEqual(changed("gira gira in locale", "gira in locale"), [
    ["-", "gira "],
  ]);
  assert.deepEqual(changed("latenze evidenti.", "latenze."), [
    ["-", " evidenti"],
  ]);
});

test("sentences end after punctuation followed by space, or at a line break", () => {
  const text = "Ciao Marco. Va bene? Sì!\nA dopo 3.5 volte";
  assert.deepEqual(sentenceStarts(text), [0, 12, 21, 25]);
  assert.deepEqual(sentenceStarts("Fine."), [0]);
  // A prefix keeps the sentence numbers of the text it grows into.
  assert.deepEqual(sentenceStarts("Ciao."), [0]);
  assert.deepEqual(sentenceStarts("Ciao. Va"), [0, 6]);
});

test("changes are assigned to the sentence they come from and rebuild the text", () => {
  const produced = "Uno tauri. Due flo. Tre.";
  const current = "Uno Tauri. Due Flow e mezzo. Tre.";
  const texts = sentenceTexts(produced, current);
  assert.deepEqual(texts, ["Uno Tauri. ", "Due Flow e mezzo. ", "Tre."]);
  assert.equal(texts.join(""), current);
  // Merging two sentences: each keeps its own part of the change.
  const merged = sentenceTexts(produced, "Uno tauri, due flo. Tre.");
  assert.equal(merged.join(""), "Uno tauri, due flo. Tre.");
  assert.equal(merged.length, 3);
  const parts = partsBySentence(produced, "Uno tauri, due flo. Tre.");
  assert.ok(parts[0].some(([kind]) => kind !== "="));
  assert.ok(parts[1].some(([kind]) => kind !== "="));
});

test("lessons: style, cleanup, content and recognition", () => {
  assert.equal(
    lessonFor("ciao marco come va", "Ciao Marco, come va?"),
    "style",
  );
  assert.equal(lessonFor("gira gira bene", "gira bene"), "cleanup");
  assert.equal(lessonFor("allora ehm partiamo", "allora partiamo"), "cleanup");
  assert.equal(lessonFor("una call giovedì", "una call giovedì alle 15"), null);
  assert.equal(lessonFor("una call lunga giovedì", "una call giovedì"), null);
  assert.equal(
    lessonFor("progetto voice flow", "progetto VoiceFlow"),
    "recognition",
  );
  assert.equal(lessonFor("uguale", "uguale"), null);
  // Several kinds in one sentence: tidy edits stay tidy, a real fix still teaches.
  assert.equal(
    lessonFor("con tauri gira gira bene", "con Tauri gira bene"),
    "cleanup",
  );
  assert.equal(lessonFor("gira bene", "girava bene"), "recognition");
  assert.equal(
    lessonFor("il voice flow va", "il VoiceFlow va, bene"),
    "recognition",
  );
  assert.equal(lessonFor("Ciao. Va bene", "Ciao. Va bene davvero"), null);
});

test("steps are cumulative per sentence and end on the draft", () => {
  const produced = "Uno tauri. Due flo. Tre.";
  const draft = "Uno Tauri. Due Flow. Tre e quattro.";
  const steps = buildSteps(produced, produced, draft, (_k, before, after) =>
    lessonFor(before, after),
  );
  assert.deepEqual(
    steps.map(({ segment, text, category, restore }) => [
      segment,
      text,
      category,
      restore,
    ]),
    [
      [0, "Uno Tauri. Due flo. Tre.", "style", false],
      [1, "Uno Tauri. Due Flow. Tre.", "recognition", false],
      [2, draft, null, false],
    ],
  );
  // From a saved state: only the new difference is a step; going back is a restore.
  const restored = buildSteps(
    produced,
    draft,
    "Uno tauri. Due Flow. Tre e quattro.",
    () => "style",
  );
  assert.deepEqual(
    restored.map(({ segment, category, restore }) => [
      segment,
      category,
      restore,
    ]),
    [[0, null, true]],
  );
  assert.deepEqual(
    buildSteps(produced, draft, draft, () => null),
    [],
  );
});

test("sentence changes list only what differs from the produced text", () => {
  const changes = sentenceChanges(
    "Uno tauri. Due. Tre.",
    "Uno Tauri. Due. Tre!",
  );
  assert.deepEqual(
    changes.map(({ segment, before, after }) => [segment, before, after]),
    [
      [0, "Uno tauri. ", "Uno Tauri. "],
      [2, "Tre.", "Tre!"],
    ],
  );
});
