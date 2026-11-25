// Keep this module dependency-free: scripts/errors.test.ts imports it in Node.
import { analysisErrors } from "./analysis-errors.ts";

const GENERIC_MESSAGE = "Si è verificato un errore imprevisto. Riprova.";

// Technical messages that can reach the UI from Tauri IPC, Rust std/sqlite
// errors or the native helpers. Already-Italian backend messages pass through.
const KNOWN_MESSAGES: ReadonlyArray<readonly [RegExp, string]> = [
  [
    /\bcommand\b.*\bnot found\b|\bunknown command\b/i,
    "Funzione non disponibile in questa versione di VoiceFlow.",
  ],
  [
    /\binvalid args\b|\bmissing required key\b|\bfailed to deserialize\b|\binvalid type\b/i,
    "Richiesta non valida. Aggiorna VoiceFlow e riprova.",
  ],
  [
    /\bhelper[- ]unavailable\b/i,
    "Helper di analisi non disponibile. Usa la build firmata di VoiceFlow.",
  ],
  [
    /\b(accessibility denied|permission denied|operation not permitted|not authorized|os error (1|13))\b/i,
    "Permesso negato da macOS. Controlla le autorizzazioni in Impostazioni di Sistema.",
  ],
  [
    /\b(timed? ?out|timeout|deadline exceeded)\b/i,
    "L'operazione ha richiesto troppo tempo. Riprova.",
  ],
  [
    /\b(network|connection (refused|reset|closed)|could not resolve|dns error|failed to fetch|offline)\b/i,
    "Connessione non disponibile. Controlla la rete e riprova.",
  ],
  [
    /\b(database is locked|database table is locked|resource busy|device or resource busy)\b/i,
    "VoiceFlow è occupato, riprova tra un istante.",
  ],
  [
    /\b(no space left on device|disk full|os error 28)\b/i,
    "Spazio su disco esaurito. Libera spazio e riprova.",
  ],
  [/\bmicrophone failed to start\b/i, "Impossibile avviare il microfono."],
  [
    /\bcannot create (keyboard source|paste event)\b|\bmissing destination\b|\bfocus changed\b/i,
    "Impossibile incollare nell'app attiva. Il testo resta disponibile in VoiceFlow.",
  ],
  [/\bclipboard unavailable\b/i, "Appunti non disponibili."],
  [
    /\b(no such file or directory|os error 2)\b/i,
    "File necessario non trovato. Riapri VoiceFlow.",
  ],
];

const TECHNICAL_MARKERS =
  /\n\s+at\s|::|[{}[\]<>]|\bos error\b|\b0x[0-9a-f]+\b|\/Users\/|\bpanicked\b|\bundefined\b|\bnull\b|\bNaN\b/;

function rawMessage(error: unknown): string {
  if (typeof error === "string") return error;
  if (error instanceof Error) return error.message;
  if (error && typeof error === "object" && "message" in error) {
    const { message } = error as { message: unknown };
    if (typeof message === "string") return message;
  }
  return String(error ?? "");
}

function isHumanReadable(message: string) {
  return (
    message.length > 0 &&
    message.length <= 280 &&
    /^[\p{L}\d"«“']/u.test(message) &&
    !TECHNICAL_MARKERS.test(message)
  );
}

function knownMessage(message: string) {
  for (const [pattern, text] of KNOWN_MESSAGES)
    if (pattern.test(message)) return text;
  return null;
}

/**
 * Splits "context: context: cause" chains (anyhow `{:#}`, Rust `.context()`)
 * into the leading readable context and the remaining cause.
 */
function splitContext(message: string) {
  const segments = message.split(": ");
  let count = 0;
  while (
    count < segments.length - 1 &&
    isHumanReadable(segments[count]) &&
    !knownMessage(segments[count])
  )
    count++;
  return {
    context: segments.slice(0, count).join(": "),
    cause: segments.slice(count).join(": "),
  };
}

/** Turns any thrown value into a short Italian message suitable for the UI. */
export function toUserMessage(error: unknown): string {
  const message = rawMessage(error)
    .trim()
    .replace(/^(?:(?:Uncaught\s+)?Error:\s*)+/i, "")
    .trim();
  if (!message) return GENERIC_MESSAGE;
  if (Object.prototype.hasOwnProperty.call(analysisErrors, message))
    return analysisErrors[message];
  const { context, cause } = splitContext(message);
  if (context) {
    // Readable chains (e.g. Italian backend context) pass through verbatim;
    // otherwise keep the context and translate only the technical cause.
    if (isHumanReadable(message)) return message;
    const known = knownMessage(cause);
    if (known) return `${context}: ${known}`;
    return /[.!?]$/.test(context) ? context : `${context}.`;
  }
  const known = knownMessage(message);
  if (known) return known;
  return isHumanReadable(message) ? message : GENERIC_MESSAGE;
}
