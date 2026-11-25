// Build once, then exercise compiled assets with intercepted requests. No server.
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";
import { mkdirSync, writeFileSync } from "node:fs";

const root = resolve(import.meta.dirname, "..");
const suites = [
  "dictionary",
  "analysis",
  "scheduler",
  "analysis-runs",
  "history",
  "confidence",
  "suggestions",
  "voice-profile",
  "query-cache",
  "overlays",
  "navigation",
];
const results: { suite: string; status: number | null }[] = [];
function run(command: string, args: string[]) {
  const result = spawnSync(command, args, {
    cwd: root,
    stdio: "inherit",
    env: process.env,
  });
  if (result.error) throw result.error;
  return result.status;
}
if (run("pnpm", ["desktop:ui"]) !== 0) process.exit(1);
for (const suite of suites) {
  console.log(`\nUI suite: ${suite}`);
  const status = run(process.execPath, [
    resolve(root, `scripts/verify-${suite}-ui.ts`),
  ]);
  results.push({ suite, status });
  if (status !== 0) process.exitCode = 1;
}
const stateStatus = run(process.execPath, [
  resolve(root, "scripts/verify-state-renders.ts"),
]);
results.push({ suite: "state-renders", status: stateStatus });
if (stateStatus !== 0) process.exitCode = 1;
mkdirSync(resolve(root, "artifacts/ui"), { recursive: true });
writeFileSync(
  resolve(root, "artifacts/ui/result.json"),
  JSON.stringify({ results }, null, 2) + "\n",
);
