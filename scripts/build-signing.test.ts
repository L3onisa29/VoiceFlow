import { test } from "node:test";
import assert from "node:assert/strict";
import { selectIdentity, checkRequirement } from "./build-signing.ts";
const requirement =
  'identifier "com.voiceflow.desktop" and anchor apple generic and certificate leaf[subject.OU] = "PERSONALTEAM"';
test("rejects the old per-executable ad-hoc requirement", () => {
  assert.throws(() =>
    checkRequirement(
      '# designated => cdhash H"3717538075c814ab8c3c0d573ce742759e5a4549"',
    ),
  );
});
test("requires and retains the configured signer without fallback", () => {
  assert.throws(() => selectIdentity(undefined));
  assert.throws(() => selectIdentity(undefined, "-"));
  assert.equal(selectIdentity({ identity: "stable-key" }), "stable-key");
  assert.throws(() => selectIdentity({ identity: "stable-key" }, "other-key"));
});
test("accepts updated executable hashes with exactly the same designated requirement", () => {
  for (const hash of ["old-bytes", "new-bytes"]) {
    assert.equal(
      checkRequirement(
        `CDHash=${hash}\ndesignated => ${requirement}`,
        requirement,
      ),
      requirement,
    );
  }
  assert.throws(() =>
    checkRequirement(
      `designated => ${requirement.replace("PERSONALTEAM", "OTHERTEAM")}`,
      requirement,
    ),
  );
});
