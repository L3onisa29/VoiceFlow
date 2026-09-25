import { join } from "node:path";

export const helperBuild = [
  "build",
  "--release",
  "--locked",
  "-p",
  "voiceflow-desktop",
  "--bin",
  "voiceflow-analysis",
];
export function helperPackaging(
  root: string,
  bundle: string,
  identity: string,
) {
  return {
    source: join(root, "target/release/voiceflow-analysis"),
    destination: join(bundle, "Contents/MacOS/voiceflow-analysis"),
    signHelper: [
      "--force",
      "--sign",
      identity,
      "--identifier",
      "com.voiceflow.desktop.analysis",
      "--options",
      "runtime",
      join(bundle, "Contents/MacOS/voiceflow-analysis"),
    ],
    signBundle: [
      "--force",
      "--sign",
      identity,
      "--options",
      "runtime",
      "--entitlements",
      join(root, "apps/desktop/src-tauri/Entitlements.plist"),
      bundle,
    ],
  };
}
