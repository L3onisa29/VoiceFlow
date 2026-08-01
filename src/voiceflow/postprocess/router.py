from __future__ import annotations

from dataclasses import dataclass

from voiceflow.config import RoutingConfig
from voiceflow.postprocess.base import (
    PostProcessor,
    PostprocessRequest,
    PostprocessResult,
)
from voiceflow.processing import PostprocessRoute, ProcessingIntent, RouteReason


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    route: PostprocessRoute
    reason: RouteReason


class PostprocessRouter:
    def __init__(
        self,
        provider: PostProcessor,
        config: RoutingConfig,
    ) -> None:
        self.provider = provider
        self.config = config

    def decide(self, request: PostprocessRequest) -> RoutingDecision:
        if self.provider.name == "none":
            return RoutingDecision(PostprocessRoute.RULES_ONLY, RouteReason.PROVIDER_DISABLED)
        if self.config.mode == "rules_only":
            return RoutingDecision(PostprocessRoute.RULES_ONLY, RouteReason.RULES_ONLY_MODE)
        if request.processing_intent == ProcessingIntent.DEEP:
            return RoutingDecision(PostprocessRoute.PROVIDER, RouteReason.DEEP_REQUESTED)
        if self.config.mode == "always":
            return RoutingDecision(PostprocessRoute.PROVIDER, RouteReason.ALWAYS_MODE)
        if "backtrack" in request.detected_signals:
            return RoutingDecision(PostprocessRoute.PROVIDER, RouteReason.BACKTRACK_DETECTED)
        semantic_signals = {"filler", "repetition", "semantic_need"}
        if request.detected_signals & semantic_signals:
            return RoutingDecision(PostprocessRoute.PROVIDER, RouteReason.SEMANTIC_CLEANUP)
        if len(request.text.split()) <= self.config.rules_only_max_words:
            return RoutingDecision(PostprocessRoute.RULES_ONLY, RouteReason.CLEAN_FAST_PATH)
        return RoutingDecision(PostprocessRoute.PROVIDER, RouteReason.SEMANTIC_CLEANUP)

    def process(self, request: PostprocessRequest) -> PostprocessResult:
        decision = self.decide(request)
        return self.process_decision(request, decision)

    def process_decision(
        self,
        request: PostprocessRequest,
        decision: RoutingDecision,
    ) -> PostprocessResult:
        if decision.route == PostprocessRoute.RULES_ONLY:
            return PostprocessResult(
                request.text,
                route=decision.route,
                reason=decision.reason,
            )
        result = self.provider.process(request)
        return PostprocessResult(
            result.text,
            operations=result.operations,
            route=decision.route,
            reason=decision.reason,
            used_fallback=result.used_fallback,
            fallback_code=result.fallback_code,
            error=result.error,
        )
