import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import {
  desktopRuntimeMessage,
  inspectDesktopRuntime,
} from "./desktop-runtime.ts";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const executable = process.argv[2]
  ? resolve(process.argv[2])
  : resolve(
      root,
      "target/release/bundle/macos/VoiceFlow.app/Contents/MacOS/voiceflow-desktop",
    );
const images = inspectDesktopRuntime(executable);
console.log(desktopRuntimeMessage(images));
if (images.some(({ status }) => status !== "current")) process.exitCode = 1;
