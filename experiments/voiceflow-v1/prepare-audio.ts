// File-only synthetic speech. Never captures or plays audio.
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
const corpus = JSON.parse(
  readFileSync("experiments/voiceflow-v1/boost-corpus.json", "utf8"),
);
const dir = "artifacts/boosting/audio";
mkdirSync(dir, { recursive: true });
function run(cmd: string, args: string[]) {
  const r = spawnSync(cmd, args, { encoding: "utf8" });
  if (r.status !== 0) throw new Error(r.stderr || String(r.error));
}
const hashes = [];
for (const s of corpus.sessions) {
  const p = `${dir}/${s.id}`;
  run("say", ["-v", s.voice, "-r", "180", "-o", `${p}.aiff`, s.text]);
  run("ffmpeg", [
    "-y",
    "-loglevel",
    "error",
    "-i",
    `${p}.aiff`,
    "-ar",
    "16000",
    "-ac",
    "1",
    "-c:a",
    "pcm_s16le",
    `${p}.wav`,
  ]);
  run("ffmpeg", [
    "-y",
    "-loglevel",
    "error",
    "-i",
    `${p}.wav`,
    "-f",
    "s16le",
    `${p}.raw`,
  ]);
  hashes.push({
    id: s.id,
    wavSha256: createHash("sha256")
      .update(readFileSync(`${p}.wav`))
      .digest("hex"),
  });
}
writeFileSync(
  "artifacts/boosting/audio-hashes.json",
  JSON.stringify(hashes, null, 2) + "\n",
);
const rows = [
  ["warmup", "en-US", 0, `${dir}/bc01.raw`, corpus.phrases.join(",")].join(
    "\t",
  ),
];
for (const s of corpus.sessions.filter(
  (s: { split: string }) => s.split === "calibration",
))
  for (const w of corpus.weights)
    rows.push(
      [
        s.id,
        s.language === "it" ? "it-IT" : "en-US",
        w,
        `${dir}/${s.id}.raw`,
        corpus.phrases.join(","),
      ].join("\t"),
    );
writeFileSync("artifacts/boosting/calibration.tsv", rows.join("\n") + "\n");
