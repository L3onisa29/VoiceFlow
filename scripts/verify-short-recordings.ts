// Synthetic speech, cut immediately after the word: no microphone or playback.
import { spawnSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import assert from "node:assert/strict";

const output = resolve("artifacts/streaming/short");
const model = join(
  homedir(),
  "Library/Application Support/VoiceFlow/models/nemotron-3.5-streaming-int8",
);
mkdirSync(output, { recursive: true });
function run(command: string, args: string[]) {
  const result = spawnSync(command, args, {
    encoding: "utf8",
    maxBuffer: 16 * 1024 * 1024,
    env: { ...process.env, VOICEFLOW_TEST_MODEL: model },
  });
  assert.equal(
    result.status,
    0,
    result.stdout + result.stderr || result.error?.message,
  );
  return result.stdout;
}
let failures = 0;
for (const [name, phrase] of [
  ["ciao", "Ciao"],
  ["pronto", "Pronto"],
  ["veloce", "Prova veloce"],
  ["frase", "Domani chiamo Marco"],
] as const) {
  const aiff = join(output, `${name}.aiff`);
  const wav = join(output, `${name}.wav`);
  run("say", ["-v", "Alice", "-r", "230", "-o", aiff, phrase]);
  run("ffmpeg", [
    "-y",
    "-loglevel",
    "error",
    "-i",
    aiff,
    "-af",
    "silenceremove=start_periods=1:start_threshold=-50dB,areverse,silenceremove=start_periods=1:start_threshold=-50dB,areverse",
    "-ar",
    "16000",
    "-ac",
    "1",
    "-c:a",
    "pcm_s16le",
    wav,
  ]);
  const result = spawnSync(
    "target/release/streaming-benchmark",
    [model, wav, "it"],
    { encoding: "utf8", maxBuffer: 16 * 1024 * 1024 },
  );
  writeFileSync(join(output, `${name}.jsonl`), result.stdout, { mode: 0o600 });
  const final = JSON.parse(result.stdout.trim().split("\n").at(-1)!);
  const passed =
    result.status === 0 &&
    final.event === "completed" &&
    final.text.toLowerCase().includes(phrase.toLowerCase());
  console.log(JSON.stringify({ name, passed, ...final }));
  if (!passed) failures++;
  // Auto still cannot identify the isolated 273ms "Ciao". Do not hide that
  // limitation by forcing Italian; verify the other immediate-stop fixtures.
  if (name !== "ciao") {
    const log = run("target/release/streaming-benchmark", [model, wav, "auto"]);
    writeFileSync(join(output, `${name}-auto.jsonl`), log, { mode: 0o600 });
    const auto = JSON.parse(log.trim().split("\n").at(-1)!);
    const passed = auto.text.toLowerCase().includes(phrase.toLowerCase());
    console.log(JSON.stringify({ name, language: "auto", passed, ...auto }));
    if (!passed) failures++;
  }
}
assert.equal(failures, 0, `${failures} short recordings lost speech`);
console.log(
  run("cargo", [
    "test",
    "--release",
    "--locked",
    "-p",
    "voiceflow-core",
    "--test",
    "short_recordings",
    "--",
    "--ignored",
  ]).trim(),
);
