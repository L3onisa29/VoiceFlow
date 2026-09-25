# Streaming cleanup verification

Silent checks on 2026-09-25, Apple M1, rustc 1.97.1, Node 24 and pnpm 11.

- `cargo fmt --all --check`: passed.
- `cargo test --workspace --locked`: 35 passed, 3 hardware tests ignored.
- `cargo clippy --workspace --all-targets --locked -- -D warnings`: passed.
- `pnpm desktop:check`: passed again after the Rust-only dependency repair.
- `pnpm desktop:ui`: passed again after that repair, with the existing warning about
  a JavaScript chunk over 500 kB.

Core tests exercise the real `StreamingSession` with scripted decoder snapshots,
including incomplete words, repeated snapshots, cleanup, opt-out preservation,
command boundaries, pause/resume, silence, decoder failure recovery and repeated
finish. Processor tests cover revisions within the mutable tail, long input,
long false-start runs, protected text, punctuation and adjacent commands. The
dependency repair adds regressions for `di di` revised to `di domani` and `il la`
revised to `il lo` at the oldest mutable word, punctuation/newline revisions,
commands revised across the same boundary, and 10,000-word repeat chains. Both
reported cases also pass through the real `StreamingSession` and finalization.
Settings tests cover old JSON without cleanup and enabled-setting serialization.

## CPU probe

Commands run against the final release processor:

```sh
cargo run --release --locked -p voiceflow-core --bin text-processing-benchmark -- 100000
target/release/text-processing-benchmark 1000000
```

| Input words | Mode                        | Elapsed ms | Microseconds per word | Maximum mutable words | Maximum retained words |
| ----------- | --------------------------- | ---------: | --------------------: | --------------------: | ---------------------: |
| 100,000     | Faithful, cleanup off        |     18.737 |                 0.187 |                     7 |                      8 |
| 100,000     | Cleanup and spoken commands |     24.255 |                 0.243 |                     7 |                      8 |
| 1,000,000   | Faithful, cleanup off        |    142.242 |                 0.142 |                     7 |                      8 |
| 1,000,000   | Cleanup and spoken commands |    139.999 |                 0.140 |                     7 |                      8 |

These are single-run CPU measurements, not latency guarantees or a statistical
comparison between modes. They exclude ASR, audio capture, IPC and UI snapshots.
The input strings contain 690,000 and 6,900,000 UTF-8 bytes respectively. Output
is 689,999 / 6,899,999 bytes in faithful mode and 529,999 / 5,299,999 bytes with
cleanup and commands. The revisable raw-word window never exceeded seven words.
Total retained complete source words never exceeded eight, including one frozen
lookbehind word whose rendering still depends on the revisable tail. Parsing can
briefly hold one additional incoming word. Confirmation can wait one extra word;
preview rendering remains immediate. Long repeat chains have the same bound.

## Independent verification

The first independent review found a lost-word regression at the release
boundary. A separate repair retained the frozen-source lookbehind described
above. A new read-only verifier then reviewed the complete diff and reran the
checks against the repaired code. No code blockers remain.

- 249 fragmented-input probes, 592,704 boundary-revision combinations and 1,584
  shrinking, UTF-8 and re-expanded tail probes passed.
- The independent million-word release probe measured 163 ms faithful and
  140 ms cleanup/commands, retaining at most seven mutable and eight total
  complete source words. The same measurement limitations above apply.
- `pnpm desktop:build` rebuilt the signed macOS app after the repair.
- `codesign --verify --deep --strict target/release/bundle/macos/VoiceFlow.app`
  passed with the stable Apple Development identity.

Verdict: **AUTOMATED CHECKS PASSED — MANUAL VERIFICATION PENDING**.
No microphone, speaker, playback, volume changes or app launch occurred in this
verification. The user postponed audio checks until explicitly requested.
Live dictation and native interaction remain to be checked then.
