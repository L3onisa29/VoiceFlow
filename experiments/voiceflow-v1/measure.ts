// Sequential file-only inference and scoring. No audio devices, servers or model downloads.
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { homedir } from "node:os";
const dir = "artifacts/boosting";
const corpus = JSON.parse(
  readFileSync("experiments/voiceflow-v1/boost-corpus.json", "utf8"),
);
type Row = {
  id: string;
  text: string;
  boost: number;
  corrected?: string;
  event?: string;
  [key: string]: unknown;
};
type Sample = { id: string; split: string; text: string; language: string };
const sessions: Sample[] = corpus.sessions;
function read(path: string): Row[] {
  return readFileSync(path, "utf8")
    .trim()
    .split("\n")
    .filter(Boolean)
    .map((x) => JSON.parse(x));
}
function validateResults(
  rows: Row[],
  split: string | undefined,
  weights: number[],
) {
  const expected = sessions.filter(
    (s) => split === undefined || s.split === split,
  );
  assert.equal(rows.length, expected.length * weights.length);
  for (const sample of expected)
    for (const boost of weights) {
      assert.equal(
        rows.filter((r) => r.id === sample.id && r.boost === boost).length,
        1,
        `Expected one result for ${sample.id} at ${boost}`,
      );
    }
}
function run(cmd: string, args: string[], output: string) {
  const r = spawnSync(cmd, args, {
    encoding: "utf8",
    timeout: 180000,
    maxBuffer: 32 * 1024 * 1024,
  });
  writeFileSync(output, r.stdout || "");
  writeFileSync(`${output}.stderr`, r.stderr || "");
  assert.equal(r.status, 0, r.stderr || String(r.error));
  return r.stdout;
}
function tokens(text: string): string[] {
  return text.toLocaleLowerCase("en").match(/[\p{L}\p{N}]+/gu) || [];
}
function distance(a: string, b: string): number {
  const x = tokens(a),
    y = tokens(b);
  let row = y.map((_, i) => i + 1);
  row.unshift(0);
  for (let i = 1; i <= x.length; i++) {
    const next = [i];
    for (let j = 1; j <= y.length; j++)
      next[j] = Math.min(
        next[j - 1] + 1,
        row[j] + 1,
        row[j - 1] + Number(x[i - 1] !== y[j - 1]),
      );
    row = next;
  }
  return row[y.length];
}
function alignedEdits(a: string, b: string): string[] {
  const x = tokens(a),
    y = tokens(b);
  const d = Array.from({ length: x.length + 1 }, (_, i) =>
    Array.from({ length: y.length + 1 }, (_, j) =>
      i === 0 ? j : j === 0 ? i : 0,
    ),
  );
  for (let i = 1; i <= x.length; i++)
    for (let j = 1; j <= y.length; j++)
      d[i][j] = Math.min(
        d[i - 1][j] + 1,
        d[i][j - 1] + 1,
        d[i - 1][j - 1] + Number(x[i - 1] !== y[j - 1]),
      );
  let i = x.length,
    j = y.length;
  const result: string[] = [];
  const insertions = new Map<number, number>();
  while (i || j) {
    if (i && j && d[i][j] === d[i - 1][j - 1] + Number(x[i - 1] !== y[j - 1])) {
      if (x[i - 1] !== y[j - 1])
        result.push(JSON.stringify([i - 1, x[i - 1], y[j - 1], 0]));
      i--;
      j--;
    } else if (i && d[i][j] === d[i - 1][j] + 1) {
      result.push(JSON.stringify([i - 1, x[i - 1], null, 0]));
      i--;
    } else {
      const ordinal = insertions.get(i) || 0;
      result.push(JSON.stringify([i, null, y[j - 1], ordinal]));
      insertions.set(i, ordinal + 1);
      j--;
    }
  }
  return result;
}
function classify(baseline: string, output: string, truth: string) {
  const wanted = new Set(alignedEdits(baseline, truth));
  const applied = alignedEdits(baseline, output);
  return {
    usefulCorrections: applied.filter((x) => wanted.has(x)).length,
    falseChanges: applied.filter((x) => !wanted.has(x)).length,
  };
}
const phase = process.argv[2];
if (phase === "select") {
  const rows = read(`${dir}/calibration.jsonl`).filter(
    (x) => x.event === "result" && x.id !== "warmup",
  );
  validateResults(rows, "calibration", corpus.weights);
  const totals = corpus.weights.map((boost: number) => ({
    boost,
    residualWords: rows
      .filter((r) => r.boost === boost)
      .reduce(
        (n, r) =>
          n + distance(r.text, sessions.find((s) => s.id === r.id)!.text),
        0,
      ),
  }));
  for (const boost of corpus.weights)
    assert.equal(
      rows.filter((r) => r.boost === boost).length,
      sessions.filter((s) => s.split === "calibration").length,
    );
  // Evaluate lowest positive score among tied minima, while retaining zero as control.
  const selected = [...totals]
    .filter((x) => x.boost > 0)
    .sort(
      (a, b) => a.residualWords - b.residualWords || a.boost - b.boost,
    )[0].boost;
  writeFileSync(
    `${dir}/selection.json`,
    JSON.stringify(
      {
        selected,
        totals,
        policy:
          "Minimize calibration normalized word edits among positive candidates; tie selects lower weight. Zero remains control. No evaluation transcription inspected.",
      },
      null,
      2,
    ) + "\n",
  );
  const lines = [
    [
      "warmup",
      "en-US",
      0,
      `${dir}/audio/bc01.raw`,
      corpus.phrases.join(","),
    ].join("\t"),
  ];
  for (const s of sessions.filter((s) => s.split === "evaluation"))
    for (const w of [0, selected])
      lines.push(
        [
          s.id,
          s.language === "it" ? "it-IT" : "en-US",
          w,
          `${dir}/audio/${s.id}.raw`,
          corpus.phrases.join(","),
        ].join("\t"),
      );
  writeFileSync(`${dir}/evaluation.tsv`, lines.join("\n") + "\n");
  console.log(JSON.stringify({ selected, totals }));
} else if (phase === "current") {
  const output = [];
  const model = `${homedir()}/Library/Application Support/VoiceFlow/models/nemotron-3.5-streaming-int8`;
  for (const s of sessions) {
    const text = run(
      "/usr/bin/time",
      [
        "-l",
        "target/release/streaming-benchmark",
        model,
        `${dir}/audio/${s.id}.wav`,
        s.language,
      ],
      `${dir}/current-${s.id}.jsonl`,
    );
    const last = JSON.parse(text.trim().split("\n").at(-1)!);
    assert.equal(last.event, "completed");
    output.push({ ...last, id: s.id, boost: 0 });
  }
  writeFileSync(
    `${dir}/current.jsonl`,
    output.map((x) => JSON.stringify(x)).join("\n") + "\n",
  );
} else if (phase === "score") {
  const selected = JSON.parse(
    readFileSync(`${dir}/selection.json`, "utf8"),
  ).selected;
  const current = read(`${dir}/current.jsonl`);
  const native = read(`${dir}/evaluation.jsonl`).filter(
    (x) => x.event === "result" && x.id !== "warmup",
  );
  validateResults(current, undefined, [0]);
  validateResults(native, "evaluation", [0, selected]);
  const all = [
    ...current.map((x) => ({ ...x, runtime: "current" })),
    ...native.map((x) => ({ ...x, runtime: "native" })),
  ];
  writeFileSync(
    `${dir}/transcripts.jsonl`,
    all.map((x) => JSON.stringify(x)).join("\n") + "\n",
  );
  run(
    "target/release/quality-corpus",
    [
      "crates/voiceflow-core/tests/fixtures/quality-v1.json",
      `${dir}/transcripts.jsonl`,
    ],
    `${dir}/corrected.jsonl`,
  );
  const rows = read(`${dir}/corrected.jsonl`);
  const summary = [];
  for (const runtime of ["current", "native"])
    for (const boost of runtime === "current" ? [0] : [0, selected])
      for (const corrections of [false, true]) {
        const cases = rows
          .filter(
            (r) =>
              r.runtime === runtime &&
              r.boost === boost &&
              sessions.find((s) => s.id === r.id)!.split === "evaluation",
          )
          .map((r) => {
            const s = sessions.find((s) => s.id === r.id)!;
            const output = corrections ? r.corrected! : r.text;
            const baseline = rows.find(
              (x) => x.runtime === runtime && x.boost === 0 && x.id === r.id,
            )!.text;
            return {
              id: r.id,
              reference: s.text,
              output,
              ...classify(baseline, output, s.text),
              residualWords: distance(output, s.text),
              referenceWords: tokens(s.text).length,
              exactNormalized:
                tokens(output).join(" ") === tokens(s.text).join(" "),
            };
          });
        assert.equal(
          cases.length,
          sessions.filter((s) => s.split === "evaluation").length,
        );
        summary.push({
          runtime,
          boost,
          corrections,
          usefulCorrections: cases.reduce((n, x) => n + x.usefulCorrections, 0),
          falseChanges: cases.reduce((n, x) => n + x.falseChanges, 0),
          residualWords: cases.reduce((n, x) => n + x.residualWords, 0),
          referenceWords: cases.reduce((n, x) => n + x.referenceWords, 0),
          exactSessions: cases.filter((x) => x.exactNormalized).length,
          cases,
        });
      }
  writeFileSync(`${dir}/summary.json`, JSON.stringify(summary, null, 2) + "\n");
  console.log(JSON.stringify(summary));
} else if (phase === "test") {
  assert.equal(distance("wrong", "right"), 1);
  assert.equal(distance("no no 42", "No, no 42."), 0);
  assert.equal(distance("tan stack", "TanStack"), 2);
  assert.equal(distance("", "x x"), 2);
  assert.deepEqual(classify("wrong", "other", "right"), {
    usefulCorrections: 0,
    falseChanges: 1,
  });
  assert.equal(alignedEdits("", "x x").length, 2);
  assert.deepEqual(
    classify("tan stack wrong", "TanStack other", "TanStack right"),
    { usefulCorrections: 2, falseChanges: 1 },
  );
  assert.equal(new Set(sessions.map((s) => s.id)).size, sessions.length);
  assert.throws(() => validateResults([], "calibration", [0]));
  console.log("scoring tests passed");
} else throw new Error("expected select|current|score|test");
