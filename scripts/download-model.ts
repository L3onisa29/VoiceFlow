import { createHash } from "node:crypto";
import { createReadStream, createWriteStream } from "node:fs";
import { mkdir, readFile, rename, stat } from "node:fs/promises";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { Readable, Transform } from "node:stream";
import { pipeline } from "node:stream/promises";

type Asset = {
  name: string;
  remoteName: string;
  bytes: number;
  sha256: string;
};
const manifest = JSON.parse(
  await readFile(
    new URL("../models/nemotron-manifest.json", import.meta.url),
    "utf8",
  ),
);
const destination = resolve(
  process.argv[2] ??
    join(
      homedir(),
      "Library/Application Support/VoiceFlow/models/nemotron-3.5-streaming-int8",
    ),
);
await mkdir(destination, { recursive: true });
async function valid(path: string, file: Asset) {
  try {
    if ((await stat(path)).size !== file.bytes) return false;
    const hash = createHash("sha256");
    for await (const chunk of createReadStream(path)) hash.update(chunk);
    return hash.digest("hex") === file.sha256;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return false;
    throw error;
  }
}
for (const file of manifest.files as Asset[]) {
  const path = join(destination, file.name);
  if (await valid(path, file)) {
    console.log(`Verified: ${file.name}`);
    continue;
  }
  const url = `https://huggingface.co/${manifest.repository}/resolve/${manifest.revision}/${file.remoteName}`;
  console.log(
    `Downloading ${file.name} (${Math.ceil(file.bytes / 1_000_000)} MB)`,
  );
  const response = await fetch(url);
  if (!response.ok || !response.body)
    throw new Error(`Download failed: HTTP ${response.status}`);
  let received = 0,
    reported = 0;
  const progress = new Transform({
    transform(chunk, _encoding, callback) {
      received += chunk.length;
      if (received - reported > 128_000_000) {
        console.log(`  ${Math.floor((received * 100) / file.bytes)}%`);
        reported = received;
      }
      callback(null, chunk);
    },
  });
  await pipeline(
    Readable.fromWeb(response.body as never),
    progress,
    createWriteStream(`${path}.part`, { mode: 0o600 }),
  );
  if (!(await valid(`${path}.part`, file)))
    throw new Error(`Size or SHA-256 mismatch: ${file.name}`);
  await rename(`${path}.part`, path);
}
console.log(`NVIDIA model verified: ${destination}`);
