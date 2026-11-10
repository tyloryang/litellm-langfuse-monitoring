"""Request metrics and Langfuse v4 OTLP export for a single LiteLLM worker.

Load as ``observability_callback.observability`` next to config.yaml. This
replaces langfuse_otel; enabling both would export each generation twice.
"""

import json
import ipaddress
import logging
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from litellm.integrations.custom_logger import CustomLogger
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExportResult
from opentelemetry.trace import NonRecordingSpan, SpanContext, Status, StatusCode, TraceFlags
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from prometheus_client import REGISTRY, Counter, Gauge, Histogram
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

LOG = logging.getLogger("gateway.observability")
LATENCY_BUCKETS = (.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10, 20, 40, 80, 160, 320, 640, 1800)
TPOT_BUCKETS = (.001, .005, .01, .02, .04, .06, .08, .1, .15, .25, .5, 1, 2.5, 5, 10)
LABELS = ("model",)
TTFT = Histogram("llm_gateway_ttft_seconds", "Arrival to first content/reasoning/tool delta; streaming only.", LABELS, buckets=LATENCY_BUCKETS)
TTFA = Histogram("llm_gateway_first_answer_seconds", "Arrival to first visible answer content; streaming only.", LABELS, buckets=LATENCY_BUCKETS)
TPOT = Histogram("llm_gateway_tpot_seconds", "Per-request mean (last-first)/(completion_tokens-1), including reasoning; streaming only.", LABELS, buckets=TPOT_BUCKETS)
E2E = Histogram("llm_gateway_e2e_seconds", "Request arrival to completion.", ("model", "stream"), buckets=LATENCY_BUCKETS)
SKIPPED = Counter("llm_gateway_tpot_skipped", "Requests excluded from TPOT, by bounded reason.", ("model", "reason"))
TOOLS = Counter("llm_gateway_tool_proposals", "Model-proposed tool calls; not tool executions.", LABELS)
EXPORT_ERRORS = Counter("llm_gateway_observation_errors", "Local observation construction failures.")
COMPLETIONS = Counter("llm_gateway_completions", "Completed requests, including failures.", ("model", "status"))
PRICING = Counter("llm_gateway_unpriced_requests", "Requests without configured per-token prices.", LABELS)
OTLP_RESULTS = Counter("llm_gateway_otel_exports", "OTLP HTTP batch export results.", ("status",))
OTLP_LAST_SUCCESS = Gauge("llm_gateway_otel_last_success_timestamp_seconds", "Most recent successful OTLP export.")
SOURCE_USAGE_ERRORS = Counter("llm_gateway_source_usage_errors", "Source IP accounting write failures.")
for export_status in ("success", "failure"):
    OTLP_RESULTS.labels(export_status).inc(0)

CONTEXT_TIERS = {"CONTEXT_32K", "CONTEXT_64K", "CONTEXT_128K", "CONTEXT_256K"}
HEURISTIC_TIERS = {"SIMPLE", "MEDIUM", "COMPLEX", "REASONING"}
ROUTED_MODELS = {"qwen-32k", "qwen-64k", "qwen-128k", "qwen-256k"}
REQUESTED_MODELS = {"qwen-auto"}
SIGNAL_NAMES = {"tokenCount", "codePresence", "reasoningMarkers", "technicalTerms", "simpleIndicators", "multiStepPatterns", "questionComplexity"}
ROUTE_CAUSES = {
    "prompt_cache_cost", "heuristic_scorer", "heuristic_v2", "reasoning_override", "llm_classifier",
    "capability_classifier", "jev_classifier", "llm_v2_classifier", "llm_v2_fallback",
    "heuristic_first_short_circuit", "hybrid_short_circuit", "classifier_plugin", "classifier_fallback",
    "capability_classifier_fallback", "default_model_fallback", "literal_keyword_match", "semantic_keyword_match",
    "plan_mode", "housekeeping", "modality_escalation", "modality_pin_override", "health_failover",
    "health_default_fallback", "session_affinity_pin", "session_affinity_escalation", "user_turn_continuation",
    "default_fallback", "keyword", "quality_tier", "bandit", "semantic_match", "semantic_no_match", "semantic_error",
}
ROUTE_LABELS = ("requested_model", "routed_model", "context_tier", "heuristic_tier", "route_cause")
SCORE_LABELS = ("requested_model", "routed_model", "context_tier", "heuristic_tier")
SIGNAL_LABELS = ("requested_model", "routed_model", "context_tier", "signal")
AUTO_ROUTES = Counter("llm_gateway_auto_routes", "Completed auto-routed requests. Context tier selects the model; heuristic tier is explanatory.", ROUTE_LABELS)
HEURISTIC_SCORE = Histogram("llm_gateway_heuristic_score", "Official complexity score for auto-routed requests. It does not select the model.", SCORE_LABELS, buckets=(0.05, 0.15, 0.25, 0.35, 0.45, 0.60, 0.75, 0.90, 1.0))
SIGNAL_FIRINGS = Counter("llm_gateway_route_signal_firings", "Fired heuristic dimensions. Dimensions without a signal are omitted.", SIGNAL_LABELS)
SIGNAL_WEIGHT = Gauge("llm_gateway_route_signal_weight", "Configured heuristic dimension weight read from the official scorer.", ("signal",))
_ROUTE_CONTRIBUTION = {}
_ROUTE_CONTRIBUTION_LOCK = threading.Lock()


def _bounded(value, allowed, fallback="other"):
    text = str(value or "")
    return text if text in allowed else fallback


class RouteContributionCollector:
    """Signed contribution cannot use a Prometheus counter; simpleIndicators may be negative."""

    def describe(self):
        return [GaugeMetricFamily("llm_gateway_route_signal_contribution", "Cumulative signed contribution of fired heuristic signals.", labels=list(SIGNAL_LABELS))]

    def collect(self):
        family = self.describe()[0]
        with _ROUTE_CONTRIBUTION_LOCK:
            items = list(_ROUTE_CONTRIBUTION.items())
        for labels, value in items:
            family.add_metric(list(labels), value)
        yield family


REGISTRY.register(RouteContributionCollector())


def record_auto_route(decision, heuristic):
    """Export bounded route labels only. Prompt text is never a label."""
    if not isinstance(decision, dict) or not decision.get("routed_model") and not decision.get("tier"):
        return
    requested = _bounded(decision.get("router_model_name") or decision.get("requested_model"), REQUESTED_MODELS)
    routed = _bounded(decision.get("routed_model"), ROUTED_MODELS)
    context_tier = _bounded(decision.get("tier"), CONTEXT_TIERS)
    cause = _bounded(decision.get("cause"), ROUTE_CAUSES)
    heuristic = heuristic if isinstance(heuristic, dict) else {}
    heuristic_tier = _bounded(heuristic.get("tier"), HEURISTIC_TIERS) if heuristic.get("tier") else "unknown"
    AUTO_ROUTES.labels(requested, routed, context_tier, heuristic_tier, cause).inc()
    try:
        score = float(heuristic.get("score"))
    except (TypeError, ValueError):
        score = None
    if score is not None and math.isfinite(score) and score >= 0:
        HEURISTIC_SCORE.labels(requested, routed, context_tier, heuristic_tier).observe(score)
    for item in heuristic.get("dimensions") or []:
        if not isinstance(item, dict) or item.get("name") not in SIGNAL_NAMES:
            continue
        try:
            SIGNAL_WEIGHT.labels(item["name"]).set(float(item.get("weight")))
        except (TypeError, ValueError):
            pass
        if not item.get("signal"):
            continue
        SIGNAL_FIRINGS.labels(requested, routed, context_tier, item["name"]).inc()
        try:
            contribution = float(item.get("contribution"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(contribution):
            continue
        key = (requested, routed, context_tier, item["name"])
        with _ROUTE_CONTRIBUTION_LOCK:
            _ROUTE_CONTRIBUTION[key] = _ROUTE_CONTRIBUTION.get(key, 0.0) + contribution


def canonical_ip(value):
    """Accept IP literals only; normalize IPv4-mapped IPv6 without trusting text labels."""
    try:
        address = ipaddress.ip_address(str(value).strip())
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return str(address)
    except ValueError:
        return None


def source_address(metadata, headers, trusted_cidrs=None):
    # LiteLLM fills requester_ip_address from request.client.host. Keep
    # general_settings.use_x_forwarded_for=false and FORWARDED_ALLOW_IPS empty
    # so this value remains the transport peer used for the trust decision.
    peer = canonical_ip(metadata.get("requester_ip_address"))
    if not peer:
        return {"source_ip": "unknown", "source_ip_origin": "unavailable"}
    trusted_cidrs = os.getenv("GATEWAY_TRUSTED_PROXY_CIDRS", "") if trusted_cidrs is None else trusted_cidrs
    networks = [ipaddress.ip_network(value.strip(), strict=False) for value in trusted_cidrs.split(",") if value.strip()]

    def trusted(value):
        address = ipaddress.ip_address(value)
        return any(address.version == network.version and address in network for network in networks)

    result = {"source_ip": peer, "network_peer_ip": peer, "source_ip_origin": "transport_peer"}
    if not trusted(peer):
        return result
    forwarded = headers.get("x-forwarded-for", "")
    if forwarded:
        chain = [canonical_ip(part) for part in forwarded.split(",")]
        if len(chain) > 16 or not all(chain):
            return result
        # Remove trusted hops from the right; a client-controlled leftmost
        # value must never override a nearer, untrusted hop.
        for address in reversed(chain):
            result.update(source_ip=address, source_ip_origin="trusted_forwarded_for")
            if not trusted(address):
                break
    elif canonical_ip(headers.get("x-real-ip")):
        result.update(source_ip=canonical_ip(headers["x-real-ip"]), source_ip_origin="trusted_real_ip")
    return result


class SourceUsageCollector:
    """Durable, bounded per-source counters; no prompts, headers or API keys."""

    def __init__(self, path=None, max_series=None):
        self.path = path
        self.max_series = max_series
        self.connection = None
        self.lock = threading.RLock()

    def _db(self):
        if self.connection is None:
            self.path = self.path or os.getenv("GATEWAY_SOURCE_USAGE_DB", ":memory:")
            self.max_series = max(1, int(self.max_series or os.getenv("GATEWAY_SOURCE_MAX_SERIES", "1024")))
            if self.path != ":memory:":
                path = Path(self.path)
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.connection = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
            if self.path != ":memory:":
                Path(self.path).chmod(0o600)
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS source_totals (
                    source_ip TEXT NOT NULL, model TEXT NOT NULL,
                    input INTEGER NOT NULL DEFAULT 0, output INTEGER NOT NULL DEFAULT 0,
                    input_cached INTEGER NOT NULL DEFAULT 0, output_reasoning INTEGER NOT NULL DEFAULT 0,
                    success INTEGER NOT NULL DEFAULT 0, error INTEGER NOT NULL DEFAULT 0,
                    missing INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(source_ip, model));
                CREATE TABLE IF NOT EXISTS processed_requests (request_id TEXT PRIMARY KEY, recorded_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS processed_age ON processed_requests(recorded_at);
                CREATE TABLE IF NOT EXISTS accounting_meta (key TEXT PRIMARY KEY, value REAL NOT NULL);
            """)
            with self.connection:
                self.connection.execute("INSERT OR IGNORE INTO accounting_meta VALUES ('started', ?)", (time.time(),))
        return self.connection

    def _reserve(self, source_ip, model):
        db = self._db()
        if not db.execute("SELECT 1 FROM source_totals WHERE source_ip=? AND model=?", (source_ip, model)).fetchone():
            count = db.execute("SELECT count(*) FROM source_totals WHERE source_ip != 'overflow'").fetchone()[0]
            if count >= self.max_series:
                source_ip, model = "overflow", "all"
            db.execute("INSERT OR IGNORE INTO source_totals(source_ip, model) VALUES (?, ?)", (source_ip, model))
        return source_ip, model

    def prepare(self, source_ip, model):
        with self.lock:
            db = self._db()
            with db:
                self._reserve(source_ip, model)

    def record(self, request_id, source_ip, model, raw, failed=False):
        prompt, completion = raw.get("prompt_tokens", raw.get("input_tokens")), raw.get("completion_tokens", raw.get("output_tokens"))
        measured = all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in (prompt, completion))
        cached = as_dict(raw.get("prompt_tokens_details", raw.get("input_tokens_details"))).get("cached_tokens", 0)
        reasoning = as_dict(raw.get("completion_tokens_details", raw.get("output_tokens_details"))).get("reasoning_tokens", 0)
        def subset(value, total):
            return min(value, total) if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
        values = (prompt, completion, subset(cached, prompt), subset(reasoning, completion)) if measured else (0, 0, 0, 0)
        with self.lock:
            db = self._db()
            with db:
                inserted = db.execute("INSERT OR IGNORE INTO processed_requests VALUES (?, ?)", (request_id, time.time())).rowcount
                if not inserted:
                    return
                source_ip, model = self._reserve(source_ip, model)
                db.execute("""UPDATE source_totals SET input=input+?, output=output+?,
                    input_cached=input_cached+?, output_reasoning=output_reasoning+?,
                    success=success+?, error=error+?, missing=missing+? WHERE source_ip=? AND model=?""",
                    (*values, int(not failed), int(failed), int(not measured), source_ip, model))
                db.execute("DELETE FROM processed_requests WHERE recorded_at < ?", (time.time() - 86400,))

    def describe(self):
        # Avoid touching a database during import / callback loader validation.
        return [CounterMetricFamily("llm_gateway_source_tokens", "Provider token usage by source IP.", labels=["source_ip", "model", "token_type"]),
                CounterMetricFamily("llm_gateway_source_requests", "Completed requests by source IP.", labels=["source_ip", "model", "status"]),
                CounterMetricFamily("llm_gateway_source_usage_missing", "Requests without complete token usage.", labels=["source_ip", "model"]),
                GaugeMetricFamily("llm_gateway_source_tracking_started_timestamp_seconds", "Source accounting start time."),
                GaugeMetricFamily("llm_gateway_source_usage_persistent", "1 if source counters use a persistent database.")]

    def collect(self):
        with self.lock:
            db = self._db()
            rows = db.execute("SELECT * FROM source_totals ORDER BY source_ip, model").fetchall()
            started = db.execute("SELECT value FROM accounting_meta WHERE key='started'").fetchone()[0]
        tokens, requests, missing, start, persistent = self.describe()
        for source_ip, model, inp, out, cached, reasoning, success, error, absent in rows:
            for kind, value in zip(("input", "output", "input_cached", "output_reasoning"), (inp, out, cached, reasoning)):
                tokens.add_metric([source_ip, model, kind], value)
            requests.add_metric([source_ip, model, "success"], success)
            requests.add_metric([source_ip, model, "error"], error)
            missing.add_metric([source_ip, model], absent)
        start.add_metric([], started)
        persistent.add_metric([], int(self.path != ":memory:"))
        yield from (tokens, requests, missing, start, persistent)


SOURCE_USAGE = SourceUsageCollector()
REGISTRY.register(SOURCE_USAGE)


class MonitoredOTLPExporter(OTLPSpanExporter):
    def export(self, spans):
        try:
            result = super().export(spans)
        except Exception:
            OTLP_RESULTS.labels("failure").inc()
            raise
        OTLP_RESULTS.labels("success" if result == SpanExportResult.SUCCESS else "failure").inc()
        if result == SpanExportResult.SUCCESS:
            OTLP_LAST_SUCCESS.set(time.time())
        return result


def as_dict(value):
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    return {}


def dumps(value):
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def output_delta(chunk):
    """Usage-only chunks and role announcements are not generated tokens."""
    chunk = as_dict(chunk)
    visible = False
    valid = False
    for choice in chunk.get("choices") or []:
        delta = as_dict(as_dict(choice).get("delta"))
        visible |= bool(delta.get("content"))
        valid |= bool(delta.get("content") or delta.get("reasoning_content") or delta.get("reasoning"))
        for tool in delta.get("tool_calls") or []:
            function = as_dict(as_dict(tool).get("function"))
            valid |= bool(function.get("name") or function.get("arguments"))
    # /v1/responses typed streaming events.
    if chunk.get("type") in {"response.output_text.delta", "response.reasoning_text.delta", "response.function_call_arguments.delta"}:
        valid |= bool(chunk.get("delta"))
        visible |= chunk.get("type") == "response.output_text.delta" and bool(chunk.get("delta"))
    return valid, visible


def normalize_usage(response):
    raw = as_dict(as_dict(response).get("usage"))
    prompt = raw.get("prompt_tokens", raw.get("input_tokens"))
    completion = raw.get("completion_tokens", raw.get("output_tokens"))
    prompt_details = as_dict(raw.get("prompt_tokens_details", raw.get("input_tokens_details")))
    completion_details = as_dict(raw.get("completion_tokens_details", raw.get("output_tokens_details")))
    cached = prompt_details.get("cached_tokens", 0) or 0
    reasoning = completion_details.get("reasoning_tokens", 0) or 0
    if not isinstance(prompt, int) or not isinstance(completion, int):
        return raw, {}, None
    # Langfuse usage components are exclusive; retain the original inclusive
    # provider counts in metadata so totals can be independently reconciled.
    details = {"input": max(0, prompt - cached),
               "output": max(0, completion - reasoning),
               "total": raw.get("total_tokens", prompt + completion)}
    if "cached_tokens" in prompt_details:
        details["input_cached_tokens"] = cached
    if "reasoning_tokens" in completion_details:
        details["output_reasoning_tokens"] = reasoning
    return raw, details, completion


def tpot_value(first, last, tokens, frames, streaming):
    if not streaming:
        return None, "non_stream"
    if not isinstance(tokens, int):
        return None, "missing_usage"
    if tokens <= 1:
        return None, "too_few_tokens"
    if first is None or last is None or frames < 2 or last <= first:
        return None, "coalesced_or_no_output"
    return (last - first) / (tokens - 1), None


@dataclass
class RequestState:
    started: float = field(default_factory=time.perf_counter)
    started_ns: int = field(default_factory=time.time_ns)
    first: float | None = None
    answer: float | None = None
    last: float | None = None
    frames: int = 0
    streaming: bool = False
    finished: bool = False
    metadata: dict = field(default_factory=dict)
    headers: dict = field(default_factory=dict)
    messages: object = None
    tools: object = None
    source: dict = field(default_factory=lambda: {"source_ip": "unknown", "source_ip_origin": "unavailable"})
    accounting_id: str = field(default_factory=lambda: secrets.token_hex(16))


class GatewayObservability(CustomLogger):
    def __init__(self, source_usage=None):
        super().__init__()
        self.states = {}
        self.lock = threading.Lock()
        self.finished = {}
        self.provider = None
        self.tracer = None
        self.source_usage = source_usage or SOURCE_USAGE

    def _get_tracer(self):
        if self.tracer is None:
            import base64
            public, secret = os.getenv("LANGFUSE_PUBLIC_KEY"), os.getenv("LANGFUSE_SECRET_KEY")
            host = (os.getenv("LANGFUSE_HOST") or os.getenv("LANGFUSE_BASE_URL") or "").rstrip("/")
            if not (public and secret and host):
                raise RuntimeError("LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are required")
            provider = TracerProvider(resource=Resource.create({"service.name": "litellm-gateway", "service.version": "1"}))
            exporter = MonitoredOTLPExporter(endpoint=host + "/api/public/otel/v1/traces", headers={
                "Authorization": "Basic " + base64.b64encode(f"{public}:{secret}".encode()).decode(),
                "x-langfuse-ingestion-version": "4",
            }, timeout=15)
            provider.add_span_processor(BatchSpanProcessor(exporter, schedule_delay_millis=1000))
            # A private provider avoids interfering with the caller's global OTEL SDK.
            self.provider, self.tracer = provider, provider.get_tracer("llm-gateway.observability")
        return self.tracer

    def _key(self, data):
        params = data.get("litellm_params") or {}
        return str(data.get("litellm_call_id") or params.get("litellm_call_id") or "")

    def _state(self, data):
        key = self._key(data)
        metadata = (data.get("litellm_params") or {}).get("metadata") or data.get("metadata") or {}
        key = key or metadata.get("gateway_request_id", "")
        with self.lock:
            return self.states.get(key)

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if call_type not in {"completion", "acompletion", "text_completion", "responses", "aresponses"}:
            return data
        headers = {k.lower(): v for k, v in (data.get("proxy_server_request", {}).get("headers") or {}).items()}
        metadata = data.setdefault("metadata", {})
        metadata["requested_model"] = data.get("model", "unknown")
        request_id = headers.get("x-request-id") or secrets.token_hex(16)
        metadata["gateway_request_id"] = request_id
        for header, name in (("x-session-id", "session_id"), ("x-litellm-session-id", "session_id"),
                             ("langfuse_session_id", "session_id"), ("langfuse_trace_name", "trace_name"),
                             ("langfuse_trace_user_id", "trace_user_id")):
            if headers.get(header) and not metadata.get(name):
                metadata[name] = headers[header]
        if data.get("user") and not metadata.get("trace_user_id"):
            metadata["trace_user_id"] = data["user"]
        source = source_address(metadata, headers)
        # Reserve only validated IP literals under a bounded cardinality cap.
        # Client-supplied metadata.source_ip is never used for accounting.
        try:
            self.source_usage.prepare(source["source_ip"], str(metadata["requested_model"]))
        except Exception:
            SOURCE_USAGE_ERRORS.inc()
            LOG.exception("Cannot prepare source IP accounting")
        state = RequestState(streaming=bool(data.get("stream")), metadata=dict(metadata), headers=headers,
                             messages=data.get("messages", data.get("input")), tools=data.get("tools"), source=source)
        key = self._key(data) or request_id
        with self.lock:
            cutoff = time.perf_counter() - 7200
            self.states = {k: v for k, v in self.states.items() if v.started > cutoff and not v.finished}
            self.finished = {k: t for k, t in self.finished.items() if t > time.perf_counter() - 300}
            self.states[key] = state
        return data

    async def async_post_call_streaming_iterator_hook(self, user_api_key_dict, response, request_data):
        state = self._state(request_data)
        complete = False
        try:
            async for chunk in response:
                if state is not None:
                    valid, visible = output_delta(chunk)
                    now = time.perf_counter()
                    if valid:
                        state.first = now if state.first is None else state.first
                        state.last = now
                        state.frames += 1
                    if visible and state.answer is None:
                        state.answer = now
                yield chunk
            complete = True
        finally:
            if not complete and state is not None and not state.finished:
                try:
                    self._record(request_data, None, None, None, RuntimeError("stream interrupted or client disconnected"))
                except Exception:
                    EXPORT_ERRORS.inc()
                    LOG.exception("Cannot construct interrupted-stream observation")

    def _context(self, state):
        if state.headers.get("traceparent"):
            return TraceContextTextMapPropagator().extract(state.headers)
        metadata = state.metadata
        tid = str(metadata.get("existing_trace_id") or metadata.get("trace_id") or "").replace("-", "")
        pid = str(metadata.get("parent_observation_id") or "")
        if re.fullmatch(r"[0-9a-fA-F]{32}", tid) and int(tid, 16):
            # Metadata-only continuation has an unobserved remote parent if no
            # parent ID was supplied. Prefer W3C traceparent from the application.
            parent = int(pid, 16) if re.fullmatch(r"[0-9a-fA-F]{16}", pid) and int(pid, 16) else secrets.randbits(64) or 1
            return trace.set_span_in_context(NonRecordingSpan(SpanContext(int(tid, 16), parent, True, TraceFlags(1))))
        from opentelemetry.context import Context
        return Context()

    def _record(self, kwargs, response, start_time, end_time, error=None):
        key = self._key(kwargs) or ((kwargs.get("litellm_params") or {}).get("metadata") or kwargs.get("metadata") or {}).get("gateway_request_id", "")
        with self.lock:
            if key and key in self.finished:
                return
        state = self._state(kwargs)
        if state is None:
            # SDK calls without proxy hooks still get a generation, but no
            # fabricated arrival/stream timings.
            state = RequestState(streaming=bool(kwargs.get("stream")))
            state.messages = kwargs.get("messages", kwargs.get("input"))
            state.metadata = (kwargs.get("litellm_params") or {}).get("metadata") or {}
            if isinstance(start_time, datetime):
                state.started_ns = int(start_time.timestamp() * 1e9)
                state.started -= max(0, datetime.now(start_time.tzinfo).timestamp() - start_time.timestamp())
        with self.lock:
            if state.finished:
                return
            state.finished = True
            if key:
                self.finished[key] = time.perf_counter()
        requested = str(state.metadata.get("requested_model") or (kwargs.get("litellm_params") or {}).get("model_group") or kwargs.get("model") or "unknown")
        standard = kwargs.get("standard_logging_object") or {}
        decision = {}
        if isinstance(standard, dict) and isinstance(standard.get("metadata"), dict):
            candidate = standard["metadata"].get("routing_decision")
            if isinstance(candidate, dict):
                decision = candidate
        model = str(decision.get("routed_model") or kwargs.get("model") or requested)
        elapsed = max(0, time.perf_counter() - state.started)
        E2E.labels(model, str(state.streaming).lower()).observe(elapsed)
        COMPLETIONS.labels(model, "error" if error else "success").inc()
        raw, usage, tokens = normalize_usage(response)
        try:
            self.source_usage.record(state.accounting_id, state.source["source_ip"], model, raw, failed=bool(error))
        except Exception:
            SOURCE_USAGE_ERRORS.inc()
            LOG.exception("Cannot persist source IP usage")
        tpot, reason = tpot_value(state.first, state.last, tokens, state.frames, state.streaming)
        if error:
            tpot, reason = None, "failed"
        if tpot is not None:
            TPOT.labels(model).observe(tpot)
        else:
            SKIPPED.labels(model, reason).inc()
        ttft = state.first - state.started if state.first is not None else None
        if ttft is not None:
            TTFT.labels(model).observe(ttft)
        if state.answer is not None:
            TTFA.labels(model).observe(state.answer - state.started)
        result = as_dict(response)
        choices = result.get("choices") or []
        output = [as_dict(c).get("message") for c in choices] if choices else result.get("output", result)
        proposed = sum(len(as_dict(as_dict(c).get("message")).get("tool_calls") or []) for c in choices)
        TOOLS.labels(model).inc(proposed)
        # Only accept curated metadata, not the LiteLLM internal dict (which can
        # contain request headers, credentials or unserializable SDK objects).
        metadata = {k: state.metadata[k] for k in ("gateway_request_id", "trace_name", "session_id", "trace_user_id", "tags") if k in state.metadata}
        metadata.update(state.source)
        metadata["requested_model"] = requested
        if decision:
            metadata["routed_model"] = decision.get("routed_model")
            metadata["context_tier"] = decision.get("tier")
            metadata["route_cause"] = decision.get("cause")
            metadata["route_signals"] = ", ".join(str(item) for item in decision.get("signals") or [])
        try:
            from routing_decision_log import _heuristic
            parts = []
            for message in state.messages or []:
                if isinstance(message, dict):
                    parts.append(str(message.get("content") or ""))
            content = "\n".join(parts)
            heuristic = _heuristic(content[:8000]) if content else {}
        except Exception:
            heuristic = {}
        if heuristic:
            metadata["heuristic_tier"] = heuristic.get("tier")
            metadata["heuristic_score"] = "{:.3f}".format(float(heuristic.get("score") or 0))
            metadata["heuristic_signals"] = ", ".join(heuristic.get("signals") or [])
            details = []
            for item in heuristic.get("dimensions") or []:
                if item.get("signal"):
                    details.append("{} weight={} score={} contribution={:.3f} signal={}".format(item["name"], item["weight"], item["score"], item["contribution"], item["signal"]))
            metadata["weight_details"] = "; ".join(details)
        try:
            record_auto_route(decision, heuristic)
        except Exception:
            EXPORT_ERRORS.inc()
            LOG.exception("Cannot record auto-route metrics")
        metadata.update({"usage_raw": raw, "timing_source": "gateway_stream_iterator", "stream_output_frames": state.frames,
                         "upstream_model": kwargs.get("model"),
                         "reasoning_usage_available": "reasoning_tokens" in as_dict(raw.get("completion_tokens_details", raw.get("output_tokens_details"))),
                         "ttft_seconds": ttft, "tpot_seconds": tpot, "tpot_excluded_reason": reason,
                         "tool_proposals": proposed, "stream": state.streaming, "pricing_status": "unconfigured"})
        input_price, output_price = os.getenv("GATEWAY_INPUT_COST_PER_TOKEN"), os.getenv("GATEWAY_OUTPUT_COST_PER_TOKEN")
        costs = None
        if input_price is not None and output_price is not None and usage:
            costs = {"input": raw.get("prompt_tokens", raw.get("input_tokens", 0)) * float(input_price),
                     "output": raw.get("completion_tokens", raw.get("output_tokens", 0)) * float(output_price)}
            costs["total"] = costs["input"] + costs["output"]
            metadata["pricing_status"] = "configured"
        else:
            PRICING.labels(model).inc()
        attributes = {"langfuse.observation.type": "generation", "langfuse.observation.model.name": model,
                      "langfuse.observation.input": dumps(state.messages), "langfuse.observation.output": dumps(output),
                      "langfuse.observation.metadata": dumps(metadata),
                      "langfuse.trace.metadata": dumps({**state.source, **{k: metadata[k] for k in ("requested_model", "routed_model", "context_tier", "route_cause", "route_signals", "heuristic_tier", "heuristic_score", "heuristic_signals", "weight_details") if metadata.get(k) not in (None, "")}}),
                      "client.address": state.source["source_ip"],
                      "langfuse.environment": os.getenv("LANGFUSE_TRACING_ENVIRONMENT", "wsl-local")}
        if state.tools:
            attributes["langfuse.observation.metadata.tools"] = dumps(state.tools)
        for key, attr in (("session_id", "session.id"), ("trace_user_id", "user.id"), ("trace_name", "langfuse.trace.name")):
            if state.metadata.get(key):
                attributes[attr] = str(state.metadata[key])
        tags = [str(tag) for tag in (state.metadata.get("tags") or [])]
        if metadata.get("routed_model"):
            tags.append("model:" + str(metadata["routed_model"]))
        if metadata.get("context_tier"):
            tags.append("tier:" + str(metadata["context_tier"]))
        if metadata.get("heuristic_tier"):
            tags.append("heuristic:" + str(metadata["heuristic_tier"]))
        if tags:
            attributes["langfuse.trace.tags"] = tags
        if usage:
            attributes["langfuse.observation.usage_details"] = dumps(usage)
        if costs:
            attributes["langfuse.observation.cost_details"] = dumps(costs)
        if ttft is not None:
            # Match the Langfuse SDK's JSON encoding of datetime attributes,
            # including the surrounding quotes.
            attributes["langfuse.observation.completion_start_time"] = dumps(datetime.fromtimestamp((state.started_ns / 1e9) + ttft, timezone.utc).isoformat())
        params = kwargs.get("optional_params") or {}
        model_params = {k: params[k] for k in ("temperature", "top_p", "max_tokens", "max_completion_tokens", "seed", "tool_choice") if k in params}
        if model_params:
            attributes["langfuse.observation.model.parameters"] = dumps(model_params)
        name = str(state.metadata.get("generation_name") or f"chat {model}")
        span = self._get_tracer().start_span(name, context=self._context(state), start_time=state.started_ns, attributes=attributes)
        if error:
            message = f"{type(error).__name__}: {str(error)[:800]}"
            # Export a bounded exception description, never request headers.
            message = re.sub(r"(?:sk-|Bearer\s+)[\w.-]+", "[REDACTED]", message)
            span.set_attribute("langfuse.observation.level", "ERROR")
            span.set_attribute("langfuse.observation.status_message", message)
            span.set_status(Status(StatusCode.ERROR, message))
        else:
            span.set_status(Status(StatusCode.OK))
        span.end(end_time=time.time_ns())
        with self.lock:
            self.states.pop(self._key(kwargs) or state.metadata.get("gateway_request_id", ""), None)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        try:
            self._record(kwargs, response_obj, start_time, end_time)
        except Exception:
            EXPORT_ERRORS.inc()
            LOG.exception("Cannot construct gateway observation")

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        try:
            self._record(kwargs, response_obj, start_time, end_time, kwargs.get("exception") or RuntimeError("upstream request failed"))
        except Exception:
            EXPORT_ERRORS.inc()
            LOG.exception("Cannot construct gateway failure observation")


observability = GatewayObservability()
