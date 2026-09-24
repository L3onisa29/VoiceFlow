import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

let failed = false;
const commands: [string, string, string[]][] = [
  ["Node 24", "node", ["--version"]],
  ["pnpm", "pnpm", ["--version"]],
  ["Rust", "rustc", ["--version"]],
  ["Cargo", "cargo", ["--version"]],
  ["Rustfmt", "cargo", ["fmt", "--version"]],
  ["Clippy", "cargo", ["clippy", "--version"]],
  ["Xcode", "xcodebuild", ["-version"]],
  ["macOS SDK", "xcrun", ["--sdk", "macosx", "--show-sdk-version"]],
  ["Clang", "xcrun", ["--find", "clang"]],
];
for (const [label, command, args] of commands) {
  const result = spawnSync(command, args, { encoding: "utf8" });
  const ok =
    result.status === 0 &&
    (label !== "Node 24" || result.stdout.trim().startsWith("v24."));
  console.log(
    `${ok ? "OK" : "MISSING"} ${label}: ${(result.stdout || result.stderr || result.error?.message || "").trim().split("\n")[0]}`,
  );
  failed ||= !ok;
}
const manifest = JSON.parse(
  readFileSync(
    new URL("../models/nemotron-manifest.json", import.meta.url),
    "utf8",
  ),
);
const path = join(
  homedir(),
  "Library/Application Support/VoiceFlow/models/nemotron-3.5-streaming-int8",
);
console.log(
  `${manifest.files.every((f: { name: string }) => existsSync(join(path, f.name))) ? "OK" : "OPTIONAL"} NVIDIA model files: ${path}`,
);
console.log(
  "Rustup and CMake are not required by this build: Homebrew Rust and prebuilt ONNX Runtime are used.",
);
process.exitCode = failed ? 1 : 0;
