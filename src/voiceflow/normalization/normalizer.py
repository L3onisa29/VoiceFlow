from __future__ import annotations

import logging
import re
from collections.abc import Callable

from voiceflow.dictionary.service import DictionaryService
from voiceflow.normalization.contracts import (
    NormalizationOperation,
    NormalizationRequest,
    NormalizationResult,
    ProtectedSpan,
)

BACKTRACK_CUES = ("anzi", "no aspetta", "mi correggo", "correzione", "volevo dire")
LOGGER = logging.getLogger(__name__)
FILLERS = frozenset({"cioè", "ehm", "uhm", "diciamo"})
DIGITS = {
    "zero": "0",
    "uno": "1",
    "una": "1",
    "due": "2",
    "tre": "3",
    "quattro": "4",
    "cinque": "5",
    "sei": "6",
    "sette": "7",
    "otto": "8",
    "nove": "9",
}
SMALL_NUMBERS = {
    **DIGITS,
    "dieci": "10",
    "undici": "11",
    "dodici": "12",
    "tredici": "13",
    "quattordici": "14",
    "quindici": "15",
    "sedici": "16",
    "diciassette": "17",
    "diciotto": "18",
    "diciannove": "19",
    "venti": "20",
    "trenta": "30",
    "quaranta": "40",
    "cinquanta": "50",
    "sessanta": "60",
}


class DeterministicNormalizer:
    def __init__(self, dictionary: DictionaryService | None = None) -> None:
        self.dictionary = dictionary

    def normalize(self, request: NormalizationRequest) -> NormalizationResult:
        text = request.text.strip()
        operations: list[NormalizationOperation] = []
        signals = self._signals(text, backtrack=request.backtrack)
        if self.dictionary is not None:
            try:
                replaced, dictionary_operations = self.dictionary.replace(
                    text,
                    language=request.language,
                    app_bundle_id=request.app_bundle_id,
                )
            except Exception:
                self.dictionary.enabled = False
                LOGGER.exception("Dictionary disabled after a local storage failure")
            else:
                operations.extend(
                    NormalizationOperation(
                        "dictionary_replacement",
                        operation.source,
                        operation.replacement,
                        operation.start,
                        operation.end,
                    )
                    for operation in dictionary_operations
                )
                text = replaced
        if request.spoken_punctuation:
            text = self._spoken_punctuation(text, operations)
        if request.explicit_lists:
            text = self._explicit_list(text, operations)
        if request.structured_tokens:
            text = self._structured_tokens(text, operations)
        cleaned = self._whitespace(text)
        if cleaned != text:
            operations.append(NormalizationOperation("whitespace", text, cleaned))
        protected = self._protected_spans(cleaned)
        return NormalizationResult(
            text=cleaned,
            operations=tuple(operations),
            detected_signals=signals,
            protected_spans=protected,
        )

    @staticmethod
    def _signals(text: str, *, backtrack: bool) -> frozenset[str]:
        folded = text.casefold()
        signals: set[str] = set()
        if backtrack and any(re.search(rf"\b{re.escape(cue)}\b", folded) for cue in BACKTRACK_CUES):
            signals.add("backtrack")
        words = re.findall(r"\w+", folded, re.UNICODE)
        if any(word in FILLERS for word in words):
            signals.add("filler")
        if any(
            left == right and left != "slash" for left, right in zip(words, words[1:], strict=False)
        ):
            signals.add("repetition")
        if len(words) > 24 or (len(words) > 12 and not re.search(r"[.!?]$", text.strip())):
            signals.add("semantic_need")
        return frozenset(signals)

    @staticmethod
    def _substitute(
        text: str,
        pattern: str,
        replacement: str | Callable[[re.Match[str]], str],
        operation_type: str,
        operations: list[NormalizationOperation],
        *,
        flags: int = re.IGNORECASE | re.UNICODE,
    ) -> str:
        def replace_match(match: re.Match[str]) -> str:
            value = replacement(match) if callable(replacement) else replacement
            operations.append(
                NormalizationOperation(
                    operation_type,
                    match.group(),
                    value,
                    match.start(),
                    match.end(),
                )
            )
            return value

        return re.sub(pattern, replace_match, text, flags=flags)

    def _spoken_punctuation(self, text: str, operations: list[NormalizationOperation]) -> str:
        replacements = (
            (r"\bnuovo paragrafo\b", "\n\n"),
            (r"\bpunto e a capo\b", ".\n"),
            (r"\bpunto interrogativo\b", "?"),
            (r"\bpunto esclamativo\b", "!"),
            (r"\bapri parentesi\b", " ("),
            (r"\bchiudi parentesi\b", ") "),
            (r"\bdue punti\b", ":"),
            (r"\bpunto e virgola\b", ";"),
            (r"\bvirgola\b", ","),
            (r"\bpunto\b(?=\s*$)", "."),
        )
        for pattern, replacement in replacements:
            text = self._substitute(text, pattern, replacement, "spoken_punctuation", operations)
        return text

    def _explicit_list(self, text: str, operations: list[NormalizationOperation]) -> str:
        match = re.fullmatch(r"\s*elenco\s*:\s*(.+)", text, re.IGNORECASE | re.DOTALL)
        if not match:
            return text
        items = [item.strip() for item in re.split(r"\s*;\s*", match.group(1)) if item.strip()]
        if len(items) < 2:
            return text
        replacement = "\n".join(f"- {item}" for item in items)
        operations.append(NormalizationOperation("explicit_list", text, replacement, 0, len(text)))
        return replacement

    def _structured_tokens(self, text: str, operations: list[NormalizationOperation]) -> str:
        def email(match: re.Match[str]) -> str:
            body = match.group(1).casefold()
            parts = body.split()
            output: list[str] = []
            for part in parts:
                output.append({"chiocciola": "@", "punto": "."}.get(part, part))
            return "".join(output).rstrip(".,;")

        text = self._substitute(
            text,
            r"\bemail\s+((?:[\w+-]+\s+)*(?:chiocciola)\s+(?:[\w-]+\s+)*(?:punto)\s+\w+)",
            email,
            "structured_email",
            operations,
        )

        def url(match: re.Match[str]) -> str:
            body = match.group(1).casefold()
            body = re.sub(r"\bdue punti\b", ":", body)
            body = re.sub(r"\bslash\b", "/", body)
            body = re.sub(r"\bpunto\b", ".", body)
            return re.sub(r"\s+", "", body).rstrip(".,;")

        text = self._substitute(
            text,
            r"\burl\s+(https?://[^\s]+)",
            lambda match: match.group(1),
            "structured_url",
            operations,
        )
        text = self._substitute(
            text,
            (
                r"\burl\s+((?:https?\s+)?(?:(?:due punti|:)\s*)?"
                r"(?:slash\s+slash\s+)?[\w-]+(?:\s+punto\s+[\w-]+)+"
                r"(?:\s+slash\s+[\w-]+)*)"
            ),
            url,
            "structured_url",
            operations,
        )
        text = self._substitute(
            text,
            r"\bfile\s+([\w-]+)\s+punto\s+([A-Za-z0-9]{1,8})\b",
            lambda match: f"{match.group(1)}.{match.group(2).casefold()}",
            "structured_file",
            operations,
        )

        def digit_sequence(match: re.Match[str]) -> str:
            return "".join(DIGITS[word.casefold()] for word in match.group(1).split())

        digit_words = "|".join(DIGITS)
        text = self._substitute(
            text,
            rf"\bnumero\s+((?:(?:{digit_words})\s+){{1,}}(?:{digit_words}))\b",
            digit_sequence,
            "digit_sequence",
            operations,
        )

        number_words = "|".join(SMALL_NUMBERS)

        def hour(match: re.Match[str]) -> str:
            hours = SMALL_NUMBERS[match.group(1).casefold()]
            minutes = SMALL_NUMBERS[match.group(2).casefold()]
            return f"{int(hours):02d}:{int(minutes):02d}"

        text = self._substitute(
            text,
            rf"\bore\s+({number_words})\s+e\s+({number_words})\b",
            hour,
            "time",
            operations,
        )

        def scalar(match: re.Match[str]) -> str:
            return SMALL_NUMBERS.get(match.group(1).casefold(), match.group(1))

        text = self._substitute(
            text,
            rf"\b({number_words})\s+percento\b",
            lambda match: f"{scalar(match)}%",
            "percentage",
            operations,
        )
        text = self._substitute(
            text,
            rf"\b({number_words})\s+(euro|dollari)\b",
            lambda match: (
                f"{SMALL_NUMBERS[match.group(1).casefold()]} "
                f"{'€' if match.group(2).casefold() == 'euro' else '$'}"
            ),
            "currency",
            operations,
        )
        return text

    @staticmethod
    def _whitespace(text: str) -> str:
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r" +([,.;:!?])", r"\1", text)
        text = re.sub(r"([,;:!?])(?=[^\W\d_])", r"\1 ", text)
        text = re.sub(r" *\n *", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"\(\s+", "(", text)
        text = re.sub(r"\s+\)", ")", text)
        return text.strip()

    @staticmethod
    def _protected_spans(text: str) -> tuple[ProtectedSpan, ...]:
        patterns = (
            ("email", r"(?<!\w)[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?!\w)"),
            ("url", r"\bhttps?://[^\s]+"),
            (
                "file",
                r"(?<!\w)[\w-]+\.(?:pdf|docx?|xlsx?|csv|txt|md|py|js|ts)(?!\w)",
            ),
            ("number", r"(?<!\w)(?:\d[\d:.,]*\s?[%€$]?)(?!\w)"),
        )
        spans = [
            ProtectedSpan(match.group(), kind, match.start(), match.end())
            for kind, pattern in patterns
            for match in re.finditer(pattern, text, re.UNICODE)
        ]
        return tuple(sorted(spans, key=lambda span: (span.start, span.end)))
