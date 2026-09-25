import { spawnSync } from "node:child_process";
import {
  existsSync,
  copyFileSync,
  mkdirSync,
  readFileSync,
  renameSync,
  writeFileSync,
} from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  checkRequirement,
  selectIdentity,
  type SigningConfig,
} from "./build-signing.ts";
import {
  desktopRuntimeMessage,
  inspectDesktopRuntime,
} from "./desktop-runtime.ts";
import { helperBuild, helperPackaging } from "./build-analysis-helper.ts";

if (process.argv.includes("--check")) {
  if (
    !process.env.VOICEFLOW_STABLE_BUILD ||
    !process.env.APPLE_SIGNING_IDENTITY ||
    process.env.APPLE_SIGNING_IDENTITY === "-"
  )
    throw new Error(
      "Usa pnpm desktop:build: la build diretta senza firma stabile è disabilitata.",
    );
} else {
  if (process.platform !== "darwin")
    throw new Error("VoiceFlow desktop richiede macOS.");
  const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
  const configPath = join(
    homedir(),
    "Library/Application Support/VoiceFlow/build-signing.json",
  );
  const config: SigningConfig | undefined = existsSync(configPath)
    ? JSON.parse(readFileSync(configPath, "utf8"))
    : undefined;
  const identity = selectIdentity(config, process.env.APPLE_SIGNING_IDENTITY);
  const identities = spawnSync(
    "security",
    ["find-identity", "-v", "-p", "codesigning"],
    { encoding: "utf8" },
  );
  if (
    identities.status !== 0 ||
    !identities.stdout.split("\n").some((line) => line.includes(identity))
  )
    throw new Error(
      "Il certificato salvato non è disponibile nel Portachiavi. Nessun ripiego su firma ad-hoc.",
    );
  const helper = spawnSync("cargo", helperBuild, {
    cwd: root,
    stdio: "inherit",
  });
  if (helper.status !== 0) process.exit(helper.status || 1);
  const result = spawnSync(
    "pnpm",
    [
      "--filter",
      "@voiceflow/desktop",
      "tauri",
      "build",
      "--bundles",
      "app",
      "--config",
      JSON.stringify({ bundle: { macOS: { signingIdentity: identity } } }),
    ],
    {
      cwd: root,
      stdio: "inherit",
      env: {
        ...process.env,
        APPLE_SIGNING_IDENTITY: identity,
        VOICEFLOW_STABLE_BUILD: "1",
      },
    },
  );
  if (result.status !== 0) process.exit(result.status || 1);
  const bundle = join(root, "target/release/bundle/macos/VoiceFlow.app");
  const packaging = helperPackaging(root, bundle, identity);
  copyFileSync(packaging.source, packaging.destination);
  for (const args of [packaging.signHelper, packaging.signBundle]) {
    const signed = spawnSync("codesign", args, { encoding: "utf8" });
    if (signed.status !== 0)
      throw new Error(`Firma helper/bundle non riuscita: ${signed.stderr}`);
  }
  const helperVerified = spawnSync(
    "codesign",
    ["--verify", "--strict", packaging.destination],
    { encoding: "utf8" },
  );
  if (helperVerified.status !== 0)
    throw new Error(`Firma helper non valida: ${helperVerified.stderr}`);
  const verified = spawnSync(
    "codesign",
    ["--verify", "--deep", "--strict", bundle],
    { encoding: "utf8" },
  );
  if (verified.status !== 0)
    throw new Error(`Firma non valida: ${verified.stderr}`);
  const signed = spawnSync("codesign", ["-d", "-r-", bundle], {
    encoding: "utf8",
  });
  if (signed.status !== 0)
    throw new Error("Impossibile leggere l’identità della build.");
  const requirement = checkRequirement(
    signed.stdout + signed.stderr,
    config?.requirement,
  );
  mkdirSync(dirname(configPath), { recursive: true });
  writeFileSync(
    `${configPath}.tmp`,
    JSON.stringify({ identity, requirement }, null, 2) + "\n",
    { mode: 0o600 },
  );
  renameSync(`${configPath}.tmp`, configPath);
  console.log(
    "Firma VoiceFlow verificata. Identità macOS stabile e salvata per i prossimi aggiornamenti.",
  );
  try {
    console.log(
      desktopRuntimeMessage(
        inspectDesktopRuntime(join(bundle, "Contents/MacOS/voiceflow-desktop")),
      ),
    );
  } catch (error) {
    console.warn(
      "Build firmata; versione del processo attivo non verificata.",
      error,
    );
  }
}
