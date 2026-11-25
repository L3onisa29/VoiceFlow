// Production helper with disposable databases and a sentinel CLI. No launchd,
// microphone, account credentials, real provider, or personal history is used.
import assert from "node:assert/strict";
import {
  mkdtempSync,
  writeFileSync,
  existsSync,
  symlinkSync,
  mkdirSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { execFileSync } from "node:child_process";
import { DatabaseSync } from "node:sqlite";

const root = resolve(import.meta.dirname, "..");
const helper = resolve(
  process.argv[2] ?? join(root, "target/release/voiceflow-analysis"),
);
const data = mkdtempSync(join(tmpdir(), "voiceflow-native-helper-"));
const bin = join(data, "bin");
mkdirSync(bin);
const marker = join(data, "unexpected-provider");
const shellQuote = (value: string) =>
  "'" + value.replaceAll("'", "'\\''") + "'";
// The provider strips arbitrary env vars, so bake the sentinel path into the script.
writeFileSync(
  join(bin, "codex"),
  `#!/bin/sh\n/usr/bin/touch ${shellQuote(marker)}\nexit 99\n`,
  { mode: 0o700 },
);
symlinkSync("/usr/bin/true", join(bin, "node"));
const env = {
  PATH: `${bin}:/usr/bin:/bin`,
};
const launch = (manual: boolean) =>
  execFileSync(helper, ["--data", data, ...(manual ? ["--manual"] : [])], {
    env,
    timeout: 15000,
    encoding: "utf8",
  });
let scheduler: DatabaseSync | undefined;
let history: DatabaseSync | undefined;
try {
  launch(true);
  scheduler = new DatabaseSync(join(data, "analysis-scheduler.sqlite3"));
  const last = () =>
    scheduler!
      .prepare(
        "SELECT origin,reason,batch_id,ended_at FROM scheduler_invocations ORDER BY id DESC LIMIT 1",
      )
      .get();
  assert.equal(last()?.origin, "manual");
  assert.equal(last()?.reason, "consent-unavailable");
  assert.ok(last()?.ended_at);
  const initialSchedule = scheduler
    .prepare("SELECT config FROM scheduler")
    .get()?.config;
  history = new DatabaseSync(join(data, "history.sqlite3"));
  history.exec(
    "CREATE TABLE analysis_settings(id INTEGER PRIMARY KEY CHECK(id=1),json TEXT NOT NULL,privacy_version INTEGER NOT NULL DEFAULT 0)",
  );
  history.prepare("INSERT INTO analysis_settings(id,json) VALUES(1,?)").run(
    JSON.stringify({
      enabled: true,
      autoApproveHighConfidence: false,
      model: "gpt-6-luna",
      reasoningEffort: "medium",
      maxSources: 20,
      maxInputBytes: 65536,
      maxRequestsPerDay: 2,
      requestTimeoutSeconds: 10,
      maxSecondsPerDay: 300,
    }),
  );
  launch(true);
  assert.equal(last()?.reason, "no-work");
  assert.equal(last()?.origin, "manual");
  assert.equal(last()?.batch_id, null);
  assert.ok(last()?.ended_at);
  assert.equal(
    history.prepare("SELECT COUNT(*) AS count FROM analysis_attempts").get()
      ?.count,
    0,
  );
  launch(false);
  assert.equal(last()?.reason, "disabled");
  assert.equal(last()?.origin, "automatic");
  assert.equal(
    scheduler.prepare("SELECT config FROM scheduler").get()?.config,
    initialSchedule,
  );
  assert.equal(existsSync(marker), false);
  const results = scheduler
    .prepare(
      "SELECT origin,reason,batch_id FROM scheduler_invocations ORDER BY id",
    )
    .all();
  assert.equal(results.length, 3);
  const report = {
    helper,
    results,
    scheduleUnchanged: true,
    providerCalled: false,
    attempts: 0,
  };
  mkdirSync(join(root, "artifacts/native-helper"), { recursive: true });
  writeFileSync(
    join(root, "artifacts/native-helper/result.json"),
    JSON.stringify(report, null, 2) + "\n",
  );
  console.log(JSON.stringify(report, null, 2));
} finally {
  history?.close();
  scheduler?.close();
  rmSync(data, { recursive: true, force: true });
}
