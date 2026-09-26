# Streaming text cleanup and declarative catalog

The desktop settings include **Pulizia locale**, off by default. Existing settings
files without `cleanup` load with it off. Settings cannot change during a recording
or pause. Cleanup and spoken formatting commands are independent options.

Decoder markers such as `<unk>` and any other `<...>` span are always removed
in the text buffer, including when local cleanup is off. The live preview and
final transcript share this rule. Markers can contain spaces or arrive across
multiple updates. An unfinished marker is hidden in the preview; an unmatched
`<` is preserved at pause or stop. Removal keeps the decoder's original byte
offsets and processes only the mutable tail. Ordinary text spacing is preserved;
a standalone marker and its leading separator are discarded.

The core keeps seven complete raw words mutable, plus the unfinished word. With
only builtin cleanup, it also retains at most one preceding word with frozen source text. That word's
rendering can still depend on the first mutable word: for example, revising
`di di` to `di domani` must restore the first `di`. Once both source words leave
the mutable window, their deletion or command can commit. Repeated false starts
need only this one word of provenance, even through a long chain. There are at
most eight retained complete words, with one additional incoming word during
parsing. Confirmation can wait one extra word; the preview shows the processed
window and unfinished word immediately. Silence, pause and stop complete the
last word and drain the window once. A pause is an
utterance boundary: commands and cleanup do not join across it.

The builtin catalog recognizes only these lowercase Italian patterns:

- `il la scheda` becomes `la scheda`.
- `della dell'acquisizione` and the curly-apostrophe equivalent lose `della`.
- Adjacent repeats of `il`, `lo`, `la`, `gli`, `le`, `un`, `una`, `di`, `del`,
  `della`, `dello`, `dei`, `degli` and `delle` lose the first occurrence.

It preserves the surviving spelling and punctuation. A punctuation mark after
the first word or a line break between words blocks cleanup. Uppercase names,
numbers, `un uno`, `che che`, `the The`, `molto molto importante`, negation and
`quel ragionamento di prima non va bene` remain literal. `la dispositivo` stays
as dictated. The builtin catalog has no English cleanup rule, grammar correction or semantic
rewrite. The B2 terminology dictionary is separate. Cleanup adds no word boosting
or additional model. Even a small whitelist
can remove an intentional lowercase repetition; leave cleanup off when literal
word preservation matters.

With **Comandi per paragrafi e liste**, the existing six commands work in the
live preview: `a capo`, `nuovo paragrafo`, `punto elenco`, `new line`,
`new paragraph`, and `bullet point`. Commands match complete words regardless of
case. Trailing sentence punctuation is accepted; punctuation or a line break
between the two words prevents a match. A matched command is confirmed as
one unit. An unfinished command is kept literally at pause or stop. Adjacent
paragraph/list commands keep the preceding paragraph break.

Confirmed and partial strings concatenate directly. Partial text owns its
leading separator, including any newline; the UI must not insert an extra space.
Copy, error recovery and final paste use this same processed transcript. Stop
does not run the command formatter a second time. With cleanup off and faithful
formatting, words and internal whitespace remain literal except for decoder
marker removal.

The decoder adapter accepts cumulative snapshots, repeated snapshots and updates
that extend an unfinished word. Revisions within the seven-word raw tail are
reprocessed. NVIDIA's decoder accumulates tokens; revisions to the frozen source
prefix, including the lookbehind word, are outside this contract. The window is
bounded by word count, not byte count. The accumulated transcript, ASR token
history and UI snapshots still grow with the session. Only local rule checks
operate on the bounded tail. Processing
runs in the existing inference worker and adds no thread or audio queue.

## Declarative catalog and session snapshots

The 17 existing cleanup rules are data in `cleanup-builtin.json`. Their catalog
version is `cleanup-catalog-v1:builtin-1`. They retain the exact previous behavior,
including lowercase Italian matching regardless of the decoder language setting.
No new learned rule activates automatically.

The core accepts a separate immutable cleanup catalog through
`TextProcessor::with_rules` and `StreamingSession::with_rules`. It supports exact
false-start phrases of 2–7 words that delete only the first source word, exact
word repetitions, exact repetitions of a two- or three-word phrase, and the
existing apostrophe-prefix family. Every rule includes version, language, adjacent conditions/exceptions,
provenance and checked positive/negative examples. Unknown operations, executable
code, regex, generic repeated-word deletion and dependencies exceeding seven
words are rejected. The limit is 256 rules including builtins. Each rule's examples
validate that rule in isolation; publication must also check combined behavior.

Commands take precedence, followed by dictionary matches or conflicts, then
cleanup at the current original source position. No rewritten text reenters the
matcher. New adjacency created by a deletion never becomes another match. Existing
adjacent source matches can each delete their original source span. A phrase
repetition deletes the first entire phrase. At each source position, the longest
matching dependency wins, followed by positive context specificity, explicit
language, and stable rule ID. Consumed positions are skipped; output never becomes
fresh matching input. To protect dictionary priority within a multiword deletion,
a phrase is preserved if a dictionary variant could begin at any interior word
of its first occurrence. This conservative check uses the dictionary’s exact or
Unicode-lowercase index even when that variant’s later context would not match.
It avoids extending the seven-word dependency to wait for an interior dictionary
match. Commands protect the entire matched cleanup source. The survivor keeps its original
spelling and punctuation. Conditions inspect source words, including words already
rendered differently by the dictionary. A pause resets their context.

Custom catalogs use the same bounded dependency retention as the B2 dictionary:
seven revisable words and at most fifteen complete source words in total. Builtin
cleanup without dictionary keeps its eight-word bound. Long dependencies and
commands wait until their supporting source is frozen before committing output.
With cleanup disabled, injected linguistic rules do not run; dictionary and
spoken-command options remain independent.

C2 owns approval, versioned publication, disabling and rollback. Compilation is
validation, not approval. Only an explicitly published snapshot may be injected
into a new dictation; running and paused sessions retain their catalog. History
records the instantiated session's cleanup version, with processor version
`voiceflow-text-v4` for new sessions. Existing historical metadata is unchanged. See [C1 verification and integration contract](verification/voiceflow-v1-c1.md)
for the schema limits, API, priority and tests. The core does not infer whether an
explicit lowercase phrase is a name or intentional wording; review remains
necessary even when a candidate passes structural validation.

## Speech disfluency proposals

The supported shapes are additive within schema version 1; older operations and
publication archives retain their meaning. The builtin version and its 17 rules
are unchanged. Compilation alone does not approve or activate a proposal.

A `repeat` operation accepts one explicit lowercase literal word. Outside the
legacy function-word list, a positive adjacent `before` or `after` condition is
required. A `repeat-phrase` operation has a `phrase` array of two or three explicit
lowercase literal words, for example `{"kind":"repeat-phrase","phrase":["le","ultime"]}`.
It also requires a positive adjacent condition. The condition surrounds the entire
repeated source, not the gap between its two occurrences. Two copies plus right
context consume at most seven source words. Every deleted word must pass the
same literal and negation restrictions; numbers, uppercase literals and code
syntax are rejected. Examples and exceptions still undergo backend validation.

These are available proposals, not new active defaults:

| Proposed operation and condition | Before | After |
| --- | --- | --- |
| `repeat: mi`, `after: mantieni` | mi mi mantieni | mi mantieni |
| `repeat: in`, `after: caso` | in in caso | in caso |
| `repeat-phrase: [le, ultime]`, `after: chat` | le ultime le ultime chat | le ultime chat |
| Existing `remove-false-start: [del, della]`, `after: prova` | del della prova | della prova |
| Existing `remove-false-start: [del, dell'input]` | del dell'input | dell'input |

These operations remove speech disfluencies, not recognition spelling errors.
The analyzer can propose them, and the suggestion editor exposes the phrase and
its context before explicit approval and publication. Without a matching approved
rule, `mi mi`, `in in` and `le ultime le ultime` stay literal. Cleanup off bypasses
all these operations. `molto molto importante` and repeated negations remain
unchanged under the illustrated rules.

Matching preserves case and surviving punctuation. Internal punctuation and
newlines break a repeated phrase. Right context is exact: `chat,` does not satisfy
`after: chat`. Punctuation on the last source word also prevents a right-context
match. Pause/finalization boundaries never join to form a new repetition.
An intentional repetition in precisely the approved literal context is
indistinguishable from a hesitation in text alone. A lowercase name or quoted
passage containing that exact context is not semantically recognized. Narrow
conditions and the app review remain necessary. No fuzzy or semantic correction
is added. See [disfluency verification](verification/voiceflow-v1-disfluencies.md).

## Reproduce verification

These commands do not capture or play audio:

```sh
cargo test --workspace --locked
cargo fmt --all --check
cargo clippy --workspace --all-targets --locked -- -D warnings
pnpm desktop:check
pnpm desktop:ui
cargo run --release --locked -p voiceflow-core --bin text-processing-benchmark -- 100000
```

The CPU probe appends a complete word to a cumulative snapshot on each iteration,
runs the actual processor with faithful/off and cleanup/commands settings, and
reports elapsed time, output size, maximum mutable word count and maximum total
retained complete words, including the frozen-source lookbehind. It excludes
ASR, audio capture, IPC and UI rendering. It includes input-string growth and
processor allocation. Timing is informational; correctness tests have no timing
thresholds. For longer sessions, repeat with `1000000`.

The existing model benchmark accepts `--cleanup` and `--spoken-commands` after the
language argument. It reads an existing WAV without playing it:

```sh
cargo run --release --locked -p voiceflow-core --bin streaming-benchmark -- \
  "$HOME/Library/Application Support/VoiceFlow/models/nemotron-3.5-streaming-int8" \
  artifacts/streaming/it.wav it --cleanup --spoken-commands
```
