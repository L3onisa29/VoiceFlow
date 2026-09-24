// Synthetic audio only. Run from the repository root after building the benchmark.
import { spawnSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import assert from "node:assert/strict";

const output = resolve("artifacts/streaming");
const model = join(
  homedir(),
  "Library/Application Support/VoiceFlow/models/nemotron-3.5-streaming-int8",
);
mkdirSync(output, { recursive: true });
function run(command: string, args: string[]) {
  const result = spawnSync(command, args, {
    encoding: "utf8",
    maxBuffer: 16 * 1024 * 1024,
  });
  if (result.status !== 0)
    throw new Error(
      `${command} failed: ${result.stderr || result.error?.message}`,
    );
  return result.stdout;
}
const fixtures = [
  {
    language: "it",
    voice: "Alice",
    phrase:
      "Questa è una prova di dettatura in tempo reale. Il modello deve trascrivere mentre parlo, senza aspettare la fine della registrazione. Domani alle quindici e trenta devo chiamare Marco. Non inviare il documento prima della mia conferma.",
    expected: ["tempo reale", "mentre parlo", "non inviare", "conferma"],
  },
  {
    language: "en",
    voice: "Samantha",
    phrase:
      "This is a live transcription test. The application should display my words while I am speaking. Tomorrow at three thirty I need to call Marco. Do not send the document before my confirmation.",
    expected: [
      "live transcription",
      "while i am speaking",
      "not send",
      "confirmation",
    ],
  },
];
for (const fixture of fixtures) {
  const aiff = join(output, `${fixture.language}.aiff`),
    wav = join(output, `${fixture.language}.wav`);
  run("say", ["-v", fixture.voice, "-o", aiff, fixture.phrase]);
  run("ffmpeg", [
    "-y",
    "-loglevel",
    "error",
    "-i",
    aiff,
    "-ar",
    "16000",
    "-ac",
    "1",
    "-c:a",
    "pcm_s16le",
    wav,
  ]);
  const log = run("target/release/streaming-benchmark", [
    model,
    wav,
    fixture.language,
    "--realtime",
  ]);
  writeFileSync(join(output, `${fixture.language}-verified.jsonl`), log, {
    mode: 0o600,
  });
  const events = log
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line));
  const final = events.at(-1);
  assert.equal(final.event, "completed");
  assert.ok(
    events.filter((event) => event.event === "partial").length > 2,
    "Expected progressive preview",
  );
  assert.ok(
    final.firstPartialAudioSeconds < final.audioSeconds,
    "Preview must appear before stop",
  );
  for (const phrase of fixture.expected)
    assert.ok(final.text.toLowerCase().includes(phrase), `Missing: ${phrase}`);
  console.log(JSON.stringify({ language: fixture.language, ...final }));
}
if (process.argv.includes("--long")) {
  // Ten plays exceed 120 seconds. The session must keep accepting audio.
  const wav = join(output, "long.wav");
  run("ffmpeg", [
    "-y",
    "-loglevel",
    "error",
    "-stream_loop",
    "9",
    "-i",
    join(output, "it.wav"),
    "-c",
    "copy",
    wav,
  ]);
  const log = run("target/release/streaming-benchmark", [model, wav, "it"]);
  writeFileSync(join(output, "long-verified.jsonl"), log, { mode: 0o600 });
  const final = JSON.parse(log.trim().split("\n").at(-1)!);
  assert.ok(final.audioSeconds > 120);
  assert.ok(
    (final.text.toLowerCase().match(/conferma/g) ?? []).length >= 9,
    "Missing repeated speech",
  );
  console.log(JSON.stringify({ language: "it-long", ...final }));
}
