import { test } from "node:test";
import assert from "node:assert/strict";
import { toUserMessage } from "../apps/desktop/src/lib/errors.ts";

const GENERIC = "Si è verificato un errore imprevisto. Riprova.";
const PERMISSION =
  "Permesso negato da macOS. Controlla le autorizzazioni in Impostazioni di Sistema.";

test("readable backend messages starting lowercase pass through verbatim", () => {
  for (const message of [
    "prompts.json deve contenere da 1 a 5000 frasi.",
    "prompts.json non è nel formato atteso: expected value at line 1 column 1",
    "prompts.json non è nel formato atteso: missing field `prompts` at line 1 column 2",
  ]) {
    assert.equal(toUserMessage(message), message);
    assert.equal(toUserMessage(new Error(message)), message);
  }
});

test("object prototype keys are not treated as analysis error codes", () => {
  for (const key of ["constructor", "toString", "hasOwnProperty"])
    assert.equal(toUserMessage(key), key);
  assert.equal(toUserMessage("__proto__"), GENERIC);
  assert.match(toUserMessage("timeout"), /^Tempo massimo raggiunto/);
});

test("chained messages keep the readable context and map only the cause", () => {
  assert.equal(
    toUserMessage(
      "Frasi del profilo vocale non leggibili: Permission denied (os error 13)",
    ),
    `Frasi del profilo vocale non leggibili: ${PERMISSION}`,
  );
  assert.equal(
    toUserMessage(
      "Impossibile salvare: scrittura fallita: No space left on device (os error 28)",
    ),
    "Impossibile salvare: scrittura fallita: Spazio su disco esaurito. Libera spazio e riprova.",
  );
  assert.equal(
    toUserMessage("prompts.json non è nel formato atteso: expected `,` or `]`"),
    "prompts.json non è nel formato atteso.",
  );
});

test("strips Error: prefixes", () => {
  assert.equal(
    toUserMessage("Error: Microfono non disponibile."),
    "Microfono non disponibile.",
  );
  assert.equal(
    toUserMessage("Uncaught Error: Error: nessun modello caricato"),
    "nessun modello caricato",
  );
});

test("known English technical messages are mapped", () => {
  assert.equal(toUserMessage("Permission denied (os error 13)"), PERMISSION);
  assert.equal(
    toUserMessage(new Error("Command analysis_status not found")),
    "Funzione non disponibile in questa versione di VoiceFlow.",
  );
  assert.equal(
    toUserMessage(
      "invalid args `id` for command `history_get`: invalid type: string, expected i64",
    ),
    "Richiesta non valida. Aggiorna VoiceFlow e riprova.",
  );
});

test("empty, absent and unreadable errors fall back to the generic message", () => {
  for (const value of [
    undefined,
    null,
    "",
    "   ",
    "Error: ",
    {},
    "panicked at src/x.rs:1:2",
  ])
    assert.equal(toUserMessage(value), GENERIC);
});
