# VoiceFlow v1 quality and boosting experiment

This directory is an isolated measurement tool. It never changes the app runtime. All speech is synthesized into explicit files by `say -o`; `ffmpeg` converts those files. No microphone, playback, audio device change, server or hosted inference is used. Downloads, models, WAVs and result files stay under ignored `artifacts/boosting`.

## Frozen comparison and targets

The 24 text sessions have disjoint IDs: four proposal, four calibration and sixteen evaluation. The two terminology rules cite proposal IDs only. Cleanup uses the shipped catalog. Text truth is authored before execution. Evaluation failures must remain visible and require a separate decision before changing expected text. A synthetic result does not establish personal dictation quality.

Prior B2/C1 release measurements on this M1 reported 100,000 word update loops of roughly 15–41 ms, and B2 preview rendering about 458 ms with 4096 variants. Before any optimization, use these engineering investigation thresholds: under 100 ms for the same update benchmark, under 1000 ms with preview, at most seven mutable and fifteen retained source words. These thresholds allow roughly 2× measurement noise and expanded catalogs. They are not a claim about ASR latency. Require zero unexpected edits on these declared controls and report every residual edit. Do not optimize until repeated comparable measurements exceed a threshold. No optimization is included here.

The native experiment uses three phrases, four calibration sessions and four heldout sessions, with two similar-word/name controls in each split. Candidate weights are 0, 2 and 3, based on the runtime's RNNT documentation. Select the positive candidate with the fewest calibration normalized word errors, breaking ties toward the lower weight. Retain weight zero as a native control even when it wins. Freeze `selection.json` before evaluation. The forced positive choice tests feasibility; it does not recommend enabling boosting when zero ties or wins.

## Reproduce

Run at the repository root with Node 24 through nvm, Rust and Xcode command-line tools. Inspect scripts first. Model downloads total about 745 MB. macOS voices Alice and Samantha and `ffmpeg` must already exist. There is no global installation step.

```sh
sh experiments/voiceflow-v1/fetch-native.sh
source ~/.nvm/nvm.sh
nvm use 24
cargo build --release --locked -p voiceflow-core --bin quality-corpus --bin streaming-benchmark --bin text-processing-benchmark
node experiments/voiceflow-v1/measure.ts test
node experiments/voiceflow-v1/prepare-audio.ts
/usr/bin/time -l target/release/quality-corpus > artifacts/boosting/quality.jsonl 2> artifacts/boosting/quality.time
/usr/bin/time -l target/release/text-processing-benchmark 100000 > artifacts/boosting/processor.jsonl 2> artifacts/boosting/processor.time
/usr/bin/time -l target/release/text-processing-benchmark 100000 preview > artifacts/boosting/preview.jsonl 2> artifacts/boosting/preview.time
/usr/bin/time -l artifacts/boosting/native-boost artifacts/boosting/model.gguf artifacts/boosting/calibration.tsv > artifacts/boosting/calibration.jsonl 2> artifacts/boosting/calibration.stderr
node experiments/voiceflow-v1/measure.ts select
/usr/bin/time -l artifacts/boosting/native-boost artifacts/boosting/model.gguf artifacts/boosting/evaluation.tsv > artifacts/boosting/evaluation.jsonl 2> artifacts/boosting/evaluation.stderr
node experiments/voiceflow-v1/measure.ts current
node experiments/voiceflow-v1/measure.ts score
```

Run each command to completion before the next inference command. The current decoder model must already exist at the app's model directory. `current` runs the existing file benchmark sequentially for eight sessions and records its load, inference, finalization and maximum RSS per process. The native tool holds one model, does one separate warmup, then feeds 20 ms PCM16 chunks through the C streaming API. Native right context is one encoder frame, and language is explicitly `it-IT` or `en-US`. The current app's silence preroll and VAD remain in its benchmark; the native probe does not copy them. Model formats and quantization implementations also differ. Only native zero versus native boost isolates boosting.

The native library can return successful empty transcripts. Keep and score them as failed recognition, not missing data. Latencies are unpaced compute time, not live microphone-to-paste latency. Peak RSS is cumulative process memory on macOS. Native model creation uses mapped weights, so its short create time excludes later page faults. Warmup is reported separately. No timing from the corpus scoring process is a decoder timing.

## Metrics

Text measurement tokenizes alternating whitespace and non-whitespace runs, retaining punctuation, newline commands and repeated words. A deterministic minimum edit alignment compares rules-off output against declared truth and against each processed output. `usefulCorrections` counts identical expected edits at the same source offset. `falseChanges` counts all other applied edits, including an error replaced by a different error. Repeated insertions are counted independently. `residualEdits` is the distance from output to truth. Ambiguous alignments may classify a valid alternative edit conservatively as false. Counts are edit operations, not percentage accuracy or numbers of dictionary substitutions. Rules-off still applies shared commands and marker filtering, so those behaviors are evaluated by residual edits and exact output, not counted as terminology improvements.

Processor times cover updates and finalization only, after rule compilation and snapshot construction; one warmup precedes five measured repeats. Every row includes median, maximum and retained words. The separate 100,000 word benchmark measures processor memory without the quality scorer's edit matrix.

Audio scoring lowercases and extracts Unicode letter/number word tokens. It ignores punctuation and case, but does not normalize numeral words, compound terms, or aliases. Thus `ventidue` and `22` are different despite equivalent meaning. Raw output and per-session results remain available. Native and current outputs receive the same frozen terminology and cleanup through the actual `TextProcessor`; no substitutions are learned from calibration or evaluation outputs.

## Later personal collection, without audio

Use the app's new original-ASR and confirmed revision records only after the person verifies the final intended wording. Collect 20–30 independent session IDs covering the categories in the synthetic corpus. Export only explicitly selected textual records into a local ignored path. Do not use old rows without original ASR as paired ground truth. Never label automatically processed text as a confirmed correction.

Each JSONL record should use the format in `personal-session.example.json`. Copy the app's language, selected groups, model identifier and exact rule versions. The person records confirmation time and correction category, or explicitly confirms no correction was needed. Keep no audio or transient snapshots. Remove confidential material before sharing. Deleting a selected session requires deleting its corpus record and any derived outputs too.

Assign each session once to proposal, calibration or evaluation before examining its corrections. Keep related repeats from the same source/session in the same split. Propose rules only from proposal records, select configuration using calibration records, freeze the rule version and measure evaluation once. A failed evaluation remains a failure; new rules require a new corpus version and fresh heldout sessions. The synthetic runner is reproducible now; personal quality measurement remains pending collection and explicit text verification. Personal records need a separate private loader; this repository intentionally includes no personal samples.
