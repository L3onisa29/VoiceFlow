from __future__ import annotations

import time

import pytest

from voiceflow.benchmarking import percentile
from voiceflow.config import RoutingConfig
from voiceflow.normalization import DeterministicNormalizer, NormalizationRequest
from voiceflow.postprocess import PostprocessRequest, PostprocessResult
from voiceflow.postprocess.base import validate_provider_payload
from voiceflow.postprocess.router import PostprocessRouter
from voiceflow.processing import PostprocessRoute, ProcessingIntent


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("ciao punto e a capo come stai punto interrogativo", "ciao.\ncome stai?"),
        ("elenco: mele; pere; banane", "- mele\n- pere\n- banane"),
        ("email mario chiocciola esempio punto com", "mario@esempio.com"),
        ("url https due punti slash slash esempio punto com", "https://esempio.com"),
        (
            "url https due punti slash slash esempio punto com e poi continua",
            "https://esempio.com e poi continua",
        ),
        ("file report punto PDF", "report.pdf"),
        ("numero uno due tre quattro", "1234"),
        ("ore quattordici e trenta", "14:30"),
        ("venti percento e dieci euro", "20% e 10 €"),
    ],
)
def test_deterministic_normalization_positive_cases(source: str, expected: str) -> None:
    result = DeterministicNormalizer().normalize(NormalizationRequest(source))
    assert result.text == expected
    assert DeterministicNormalizer().normalize(NormalizationRequest(result.text)).text == expected


@pytest.mark.parametrize(
    "source",
    [
        "questo punto di vista resta invariato",
        "ho venti idee",
        "uno due tre senza cue",
        "mario chiocciola esempio senza cue",
        "mele; pere senza trigger elenco",
    ],
)
def test_deterministic_normalization_avoids_false_positives(source: str) -> None:
    assert DeterministicNormalizer().normalize(NormalizationRequest(source)).text == source


def test_dictionary_failure_degrades_to_rules_without_losing_text() -> None:
    class BrokenDictionary:
        enabled = True

        def replace(self, *_args, **_kwargs):
            raise OSError("database unavailable")

    dictionary = BrokenDictionary()
    result = DeterministicNormalizer(dictionary).normalize(NormalizationRequest("ciao punto"))
    assert result.text == "ciao."
    assert not dictionary.enabled


class Provider:
    name = "fake"

    def __init__(self, *, fallback: bool = False) -> None:
        self.calls: list[PostprocessRequest] = []
        self.fallback = fallback

    def process(self, request: PostprocessRequest | str) -> PostprocessResult:
        assert isinstance(request, PostprocessRequest)
        self.calls.append(request)
        return PostprocessResult(
            request.text if self.fallback else f"cleaned {request.text}",
            used_fallback=self.fallback,
            fallback_code="provider_unavailable" if self.fallback else None,
        )

    def preflight(self) -> bool:
        return not self.fallback

    def retry(self) -> bool:
        return self.preflight()

    def close(self) -> None:
        pass


def test_router_fast_path_deep_and_backtrack() -> None:
    provider = Provider()
    router = PostprocessRouter(provider, RoutingConfig())
    short = router.process(PostprocessRequest("frase breve pulita"))
    assert short.route == PostprocessRoute.RULES_ONLY
    assert provider.calls == []

    deep = router.process(
        PostprocessRequest(
            "frase breve pulita",
            processing_intent=ProcessingIntent.DEEP,
        )
    )
    assert deep.route == PostprocessRoute.PROVIDER
    assert len(provider.calls) == 1

    backtrack = router.process(
        PostprocessRequest(
            "manda 12 anzi 13",
            detected_signals=frozenset({"backtrack"}),
        )
    )
    assert backtrack.reason == "backtrack_detected"
    assert len(provider.calls) == 2


def test_deep_unavailable_and_invalid_provider_output_fall_back_normalized() -> None:
    provider = Provider(fallback=True)
    result = PostprocessRouter(provider, RoutingConfig()).process(
        PostprocessRequest("normalized", processing_intent=ProcessingIntent.DEEP)
    )
    assert result.text == "normalized"
    assert result.used_fallback
    assert result.fallback_code == "provider_unavailable"

    normalized = DeterministicNormalizer().normalize(
        NormalizationRequest("email mario chiocciola esempio punto com")
    )
    request = PostprocessRequest(
        normalized.text,
        protected_spans=normalized.protected_spans,
        allowed_operations=frozenset({"backtrack", "grammar"}),
    )
    with pytest.raises(ValueError, match="protected"):
        validate_provider_payload(
            {"text": "email rimossa", "operations": []},
            request,
        )
    output, operations = validate_provider_payload(
        {
            "text": "usa altro@example.com",
            "operations": [
                {
                    "type": "backtrack",
                    "source": "mario@esempio.com",
                    "replacement": "altro@example.com",
                }
            ],
        },
        request,
    )
    assert output == "usa altro@example.com"
    assert operations[0].type == "backtrack"


def test_normalization_and_routing_latency_budgets() -> None:
    normalizer = DeterministicNormalizer()
    provider = Provider()
    router = PostprocessRouter(provider, RoutingConfig())
    normalize_timings: list[float] = []
    route_timings: list[float] = []
    for _ in range(300):
        started = time.perf_counter()
        normalizer.normalize(
            NormalizationRequest("email mario chiocciola esempio punto com e numero uno due tre")
        )
        normalize_timings.append(time.perf_counter() - started)
        started = time.perf_counter()
        router.decide(PostprocessRequest("frase breve pulita"))
        route_timings.append(time.perf_counter() - started)
    assert percentile(normalize_timings, 0.95) <= 0.010
    assert percentile(route_timings, 0.95) <= 0.001


def test_fast_path_overhead_is_below_warm_stt_plus_100ms() -> None:
    normalizer = DeterministicNormalizer()
    provider = Provider()
    router = PostprocessRouter(provider, RoutingConfig())
    stt_timings: list[float] = []
    end_to_end: list[float] = []
    for _ in range(30):
        started = time.perf_counter()
        time.sleep(0.001)
        stt_timings.append(time.perf_counter() - started)
        normalized = normalizer.normalize(NormalizationRequest("frase breve pulita"))
        result = router.process(PostprocessRequest(normalized.text))
        end_to_end.append(time.perf_counter() - started)
        assert result.route == PostprocessRoute.RULES_ONLY
    assert provider.calls == []
    assert percentile(end_to_end, 0.95) <= percentile(stt_timings, 0.95) + 0.100
