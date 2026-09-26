# VF-04 speech disfluencies, implementation evidence

26 September 2026. Baseline `c69c6c1`, branch `codex/tauri-streaming-app`.
This report records the implementation checks; independent review is separate.
The earlier 25 September personal evaluation remains historical.

## Behavior and boundaries

`repeat` now supports an explicit safe lowercase word beyond the builtin list
when a positive adjacent condition is present. `repeat-phrase` supports two or
three explicit safe words repeated exactly, also with a positive adjacent
condition. It deletes the first entire phrase. Examples include `mi mi mantieni`
becoming `mi mantieni`, `in in caso` becoming `in caso`, and
`le ultime le ultime chat` becoming `le ultime chat`.

Existing `remove-false-start` already handles `del della` and `del dell'input`.
These are disfluency operations, not ASR spelling correction. No proposed rule
was added to the builtin catalog, activated in the app, or written to a live
application database. The 17 builtin rules and their version are unchanged.
New session metadata identifies the processor as `voiceflow-text-v4`.

Operations and archives remain schema version 1 with an additive discriminated
shape. The analyzer schema, prompt, backend validation, TypeScript schema, editor
and proposal descriptions accept the new operation. The compiler validates
examples and rejects missing positive context, invalid sizes, negations in any
deleted phrase position, numeric and uppercase literals, code syntax and unknown
fields. The JSON schemas enforce shape and bounds; the Rust compiler additionally
enforces semantic restrictions before ingest/publication.

The processor matches original source positions. Consuming a phrase advances by
its deleted word count and carries its first separator to the survivor. Longest
source dependency wins at a position, then positive context specificity,
language specificity and stable ID. Commands protect the whole matched source.
A dictionary variant that could start inside the first phrase conservatively
blocks its deletion, including conflicting variants and Unicode-lowercase
matches. This check can preserve a phrase even when the dictionary rule's later
context would not match; it avoids an unbounded overlapping dependency.
No rewritten output is processed recursively.

Seven words remain mutable, and retained source stays bounded at fifteen.
Committed output stays immutable. Conditions remain literal: punctuation on a
right-context word does not match its unpunctuated spelling. Internal punctuation,
newlines and pause boundaries prevent joining repetitions.

## Checks

- `cargo test --workspace --locked`: 172 passed, 4 ignored. This includes 57 core,
  2 quality-corpus and 113 desktop tests. Ignored audio/hardware tests were not run.
- `cargo fmt --all --check`: passed.
- `cargo clippy --workspace --all-targets --locked -- -D warnings`: passed.
- `pnpm desktop:check`: passed using Node 24.
- `pnpm desktop:ui`: passed. Vite retains the existing large-chunk warning.
- `scripts/verify-suggestions-ui.ts`: 7 checks passed against production assets
  with Tauri IPC fixtures and intercepted browser requests, without a listener.
  It verifies phrase selection, phrase/context editing, serialized save shape,
  truthful descriptions, no activation by editing, approval policy and existing
  disable/restore controls. Artifacts are in `artifacts/suggestions-disfluencies/`.

Five new core tests exercise the explicit cases, absent rules and cleanup off,
word/character snapshots, all eighteen window positions, finalization and pause,
mutable right-context revisions, bounded retention, original separators,
adjacent/overlapping repetitions, punctuation/newlines, marker stripping,
commands, dictionary priority and nonrecursive output. Negative controls cover
negation, emphasis, numbers, names, case and quoted/code tokens.

A new real-SQLite lifecycle test covers phrase ingest, edit, approval/publication,
deactivation, rollback to the active publication, archive reopening and immutable
session snapshots. History text remains unchanged. A separate fake-provider test
checks provider schema acceptance, malformed phrase rejection, unsafe semantic
rejection and unpublished proposal state. No provider was invoked.

## Private corpus replay

The actual 29 archived texts from 25 September were read from the existing ignored
`artifacts/personal-evaluation/2026-09-25/input.json`. A new reproducible probe and
its proposed catalog, per-row outputs and aggregate summary are in
`artifacts/personal-evaluation/2026-09-26-disfluencies/`. Run from the repository:

```sh
bash artifacts/personal-evaluation/2026-09-26-disfluencies/run.sh
```

The old report and probe were not overwritten. New output compares cleanup off,
builtin cleanup, the historical candidate set, the new disfluency proposals, and
new proposals combined with the earlier terminology hypotheses. The older
terminology hypotheses remain unapproved. Combining them in a private replay is
not evidence of human confirmation or app activation.

Four of 29 rows change under the new disfluency proposals relative to builtins.
Word-at-a-time and character-at-a-time replay agree with batch processing for all
29 combined outputs. Cleanup-off output is unchanged. The maximum observed bound
is seven mutable and thirteen retained source words, with immutable committed
prefixes throughout. A prepared `del della` case is covered by core tests; it is
not counted as a corpus occurrence. No personal transcript excerpts are committed.

These are text replay results, not microphone, ASR, or semantic accuracy measures.
An intentional repetition inside an approved exact context cannot be distinguished
from hesitation by these rules. The processor does not infer quoted spans or
lowercase names semantically. Proposed conditions still require review in the app.

## Independent verification and signed delivery

Verdict: **AUTOMATED CHECKS PASSED — MANUAL VERIFICATION PENDING**. A fresh,
read-only review found no blocking issue or scope deviation. No repair was needed.

Independent probes passed 32,060 source-model comparisons and 738 dictionary/
command overlap cases, including revisions, committed-prefix immutability,
punctuation, longer repetition chains, cleanup off and the 7/15 word bounds.
The reviewer reproduced all 29 personal replay outputs and confirmed that the
historical outputs and the concurrent settings patch remained unchanged.

The reviewer reran the workspace suite: 172 passed, 4 ignored. Formatting,
clippy with warnings denied, TypeScript, UI build, signing tests 4/4, runtime
tests 3/3, production UI fixtures 7/7 and frozen manifest 4/4 passed.

`pnpm desktop:build` succeeded. App deep/strict and helper strict signature
checks passed with the saved signing identity and designated requirement
unchanged. The approximately 29 MiB bundle contains the expected two arm64
executables and resources, with the audio entitlement only on the app.
App CDHash: `080e36b0ba9ca468b48289570d90cfb27e237eaf`.
Helper CDHash: `0daa5aa13823f5475ba2365a1a0365508a929099`.

The existing process, PID 84236, still uses the old executable. The reviewer did
not launch or restart the app. Real microphone and native-app verification remain
pending. Temporary independent probes and logs are in
`/tmp/voiceflow-disfluency-review/`.
