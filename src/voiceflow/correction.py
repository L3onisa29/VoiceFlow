from __future__ import annotations

import difflib
from dataclasses import dataclass

from voiceflow.dictionary import DictionaryKind, DictionaryService
from voiceflow.storage import HistoryResultSink


@dataclass(frozen=True, slots=True)
class CorrectionProposal:
    spoken_form: str
    written_form: str


def word_diff(original: str, corrected: str) -> tuple[str, ...]:
    return tuple(
        difflib.ndiff(
            original.split(),
            corrected.split(),
        )
    )


def propose_replacement(original: str, corrected: str) -> CorrectionProposal | None:
    matcher = difflib.SequenceMatcher(a=original.split(), b=corrected.split())
    changes = [opcode for opcode in matcher.get_opcodes() if opcode[0] != "equal"]
    if len(changes) != 1:
        return None
    _tag, left_start, left_end, right_start, right_end = changes[0]
    source = " ".join(original.split()[left_start:left_end])
    replacement = " ".join(corrected.split()[right_start:right_end])
    if not source or not replacement or len(source.split()) > 5 or len(replacement.split()) > 5:
        return None
    return CorrectionProposal(source, replacement)


class CorrectionService:
    def __init__(
        self,
        sink: HistoryResultSink,
        dictionary: DictionaryService | None,
    ) -> None:
        self.sink = sink
        self.dictionary = dictionary

    def save(
        self,
        corrected_text: str,
        *,
        confirm_replacement: bool = False,
        language: str | None = None,
        app_bundle_id: str | None = None,
    ) -> CorrectionProposal | None:
        outcome = self.sink.last_outcome
        corrected = corrected_text.strip()
        if outcome is None or not corrected:
            raise ValueError("No correction is available")
        final = outcome.dictation.final_text
        self.sink.save_last_correction(corrected)
        proposal = propose_replacement(final, corrected)
        if confirm_replacement and proposal is not None and self.dictionary is not None:
            self.dictionary.add(
                kind=DictionaryKind.REPLACEMENT,
                spoken_form=proposal.spoken_form,
                written_form=proposal.written_form,
                language=language,
                app_bundle_id=app_bundle_id,
            )
        return proposal
