"""Persist each route with the official heuristic weight breakdown.

The context tier still decides the model. The heuristic block is the official
weighted score: each dimension score multiplied by its configured weight.
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from litellm.integrations.custom_logger import CustomLogger

LOG = Path("/mnt/e/project/YOUR_PROJECT/litellm-langfuse-monitoring/.runtime/token-router-decisions.jsonl")
_SCORER = None


def _text(message):
    content = message.get("content") if isinstance(message, dict) else ""
    if isinstance(content, list):
        return " ".join(str(item.get("text") or "") if isinstance(item, dict) else str(item) for item in content)
    return "" if content is None else str(content)


def _joined(messages):
    if not isinstance(messages, list):
        return ""
    return "\n".join(_text(message) for message in messages if isinstance(message, dict))


def _scorer():
    global _SCORER
    if _SCORER is not None:
        return _SCORER
    from litellm import Router
    from litellm.router_strategy.complexity_router.complexity_router import ComplexityRouter
    router = Router(model_list=[{
        "model_name": "qwen-32k",
        "litellm_params": {"model": "openai/qwen-32k", "api_key": "local", "mock_response": "ok"},
    }])
    tiers = {"SIMPLE": "qwen-32k", "MEDIUM": "qwen-32k", "COMPLEX": "qwen-32k", "REASONING": "qwen-32k"}
    _SCORER = ComplexityRouter("weight-monitor", router, {"tiers": tiers}, default_model="qwen-32k", derive_savings_baseline=False)
    return _SCORER


def _heuristic(prompt):
    scorer = _scorer()
    user_text = prompt.lower()
    code_score, _ = scorer._score_keyword_match(user_text, scorer.code_keywords, "codePresence", "code", (1, 2), (0, 0.5, 1.0))
    reasoning_score, _ = scorer._score_keyword_match(user_text, scorer.reasoning_keywords, "reasoningMarkers", "reasoning", (1, 2), (0, 0.7, 1.0))
    technical_score, _ = scorer._score_keyword_match(user_text, scorer.technical_keywords, "technicalTerms", "technical", (2, 4), (0, 0.5, 1.0))
    simple_score, _ = scorer._score_keyword_match(user_text, scorer.simple_keywords, "simpleIndicators", "simple", (1, 2), (0, -1.0, -1.0))
    dimensions = [
        scorer._score_token_count(scorer._estimate_tokens(prompt)),
        code_score,
        reasoning_score,
        technical_score,
        simple_score,
        scorer._score_multi_step(user_text),
        scorer._score_question_complexity(prompt),
    ]
    weights = scorer.config.dimension_weights
    tier, score, signals, cause = scorer._score_and_classify(prompt, None)
    return {
        "tier": getattr(tier, "value", str(tier)),
        "score": score,
        "cause": cause,
        "signals": list(signals),
        "boundaries": scorer._effective_tier_boundaries(),
        "dimensions": [
            {
                "name": item.name,
                "weight": weights.get(item.name, 0),
                "score": item.score,
                "contribution": item.score * weights.get(item.name, 0),
                "signal": item.signal,
            }
            for item in dimensions
        ],
    }


class DecisionLog(CustomLogger):
    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        standard = kwargs.get("standard_logging_object") or {}
        metadata = standard.get("metadata") if isinstance(standard, dict) else {}
        if not isinstance(metadata, dict):
            metadata = {}
        messages = standard.get("messages") if isinstance(standard, dict) else None
        if messages is None:
            messages = kwargs.get("messages")
        content = _joined(messages)
        preview = content[:240]
        model = getattr(response_obj, "model", None)
        if model is None and isinstance(standard, dict):
            model = standard.get("model")
        record = {
            "time": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "content_preview": preview,
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()[:16],
            "routing_decision": metadata.get("routing_decision"),
            "heuristic": _heuristic(content[:8000]),
        }
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


handler = DecisionLog()