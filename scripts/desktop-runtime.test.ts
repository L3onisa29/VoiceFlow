import { test } from "node:test";
import assert from "node:assert/strict";
import {
  compareDesktopImages,
  desktopRuntimeMessage,
} from "./desktop-runtime.ts";

const executable =
  "/repo/target/VoiceFlow.app/Contents/MacOS/voiceflow-desktop";
const listing = (inode: string, path = executable, pid = 84236) =>
  `p${pid}\nftxt\ni${inode}\nn${path}\nftxt\ni12\nn/usr/lib/dyld\n`;

test("detects a replaced bundle even when the running path is identical", () => {
  const [image] = compareDesktopImages(
    listing("200504025"),
    executable,
    "200583337",
  );
  assert.equal(image.status, "stale");
  assert.match(desktopRuntimeMessage([image]), /esci dall’app/);
  assert.equal(
    compareDesktopImages(listing("200583337"), executable, "200583337")[0]
      .status,
    "current",
  );
});

test("distinguishes another bundle and multiple processes without trusting inode alone", () => {
  const images = compareDesktopImages(
    listing("10") +
      listing(
        "10",
        "/Applications/VoiceFlow.app/Contents/MacOS/voiceflow-desktop",
        99,
      ),
    executable,
    "10",
  );
  assert.deepEqual(
    images.map(({ status }) => status),
    ["current", "other-path"],
  );
  assert.match(desktopRuntimeMessage(images), /PID 99/);
});

test("no running app is distinct from a current app, missing identity fails visibly", () => {
  assert.deepEqual(compareDesktopImages("", executable, "10"), []);
  assert.match(desktopRuntimeMessage([]), /non è in esecuzione/);
  assert.throws(() =>
    compareDesktopImages(`p1\nftxt\nn${executable}\n`, executable, "10"),
  );
});
