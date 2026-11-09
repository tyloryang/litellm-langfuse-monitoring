#!/usr/bin/env python3
"""Supplement Strata's native /metrics with the Monitor tab's JSON fields.

No GPU driver bindings or DCGM are required on WSL. Native Strata metrics
remain a separate scrape target; this exporter never duplicates them.
"""

import json
import math
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from prometheus_client import CollectorRegistry, Gauge, generate_latest

REGISTRY = CollectorRegistry()
UP = Gauge("strata_extra_up", "Whether the last JSON poll succeeded.", registry=REGISTRY)
LAST = Gauge("strata_extra_last_success_timestamp_seconds", "Last successful JSON poll.", registry=REGISTRY)
FIELDS = {
    "gpu_power_limit_watts": ("hardware", "gpu_power_limit", 1),
    "gpu_pcie_rx_bytes_per_second": ("hardware", "gpu_pcie_rx_mb", 1048576),
    "gpu_pcie_tx_bytes_per_second": ("hardware", "gpu_pcie_tx_mb", 1048576),
    "disk_read_bytes_per_second": ("hardware", "disk_read_mb", 1048576),
    "disk_write_bytes_per_second": ("hardware", "disk_write_mb", 1048576),
    "expert_cache_bytes": ("engine", "expert_cache_mib", 1048576),
    "expert_slots": ("engine", "expert_slots", 1),
    "batch_slots": ("engine", "batch_slots", 1),
    "kv_resident_tokens": ("engine", "kv_resident", 1),
    "conversation_cache_bytes": ("conversation_cache", "bytes", 1),
    "conversation_cache_budget_bytes": ("conversation_cache", "budget_mib", 1048576),
    "conversation_cache_parked": ("conversation_cache", "parked", 1),
    "conversation_cache_evictions_total": ("conversation_cache", "evictions", 1),
    "conversation_cache_restores_total": ("conversation_cache", "restores", 1),
    "conversation_cache_requests_total": ("conversation_cache", "requests", 1),
    "conversation_cache_reused_requests_total": ("conversation_cache", "requests_reused", 1),
    "last_request_ttft_seconds": ("last", "first_engine_token_s", 1),
    "last_request_duration_seconds": ("last", "duration_s", 1),
    "last_request_prompt_tokens": ("last", "prompt_tokens", 1),
    "last_request_output_tokens": ("last", "output_tokens", 1),
    "last_request_reused_tokens": ("last", "reused", 1),
    "last_request_pcie_share_ratio": ("last", "pcie_share", 1),
}
METRICS = {name: Gauge("strata_extra_" + name, f"Strata Monitor JSON {path}.{key} (scaled by {scale}).", ("model",), registry=REGISTRY)
           for name, (path, key, scale) in FIELDS.items()}
TPOT = Gauge("strata_extra_last_request_engine_tpot_seconds", "Last engine request decode_ms/(output_tokens-1); differs from gateway stream TPOT.", ("model",), registry=REGISTRY)
PREFILL = Gauge("strata_extra_last_request_prefill_tokens_per_second", "New prompt tokens / prompt processing seconds, excluding reused tokens.", ("model",), registry=REGISTRY)
LOCK = threading.Lock()


def update(data):
    for metric in [*METRICS.values(), TPOT, PREFILL]:
        metric.clear()
    model = str((data.get("engine") or {}).get("model") or "unknown")
    data = dict(data, last=(data.get("requests") or [{}])[0])
    for name, (section, key, scale) in FIELDS.items():
        value = (data.get(section) or {}).get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            METRICS[name].labels(model).set(value * scale)
    last = data["last"]
    n, ms = last.get("output_tokens", 0), last.get("decode_ms")
    TPOT.labels(model).set(ms / 1000 / (n - 1) if n > 1 and isinstance(ms, (int, float)) else float("nan"))
    prompt_ms = last.get("prompt_ms", 0)
    PREFILL.labels(model).set(max(0, last.get("prompt_tokens", 0) - last.get("reused", 0)) / (prompt_ms / 1000) if prompt_ms > 0 else float("nan"))


def poll():
    endpoint = os.getenv("STRATA_METRICS_URL", "http://127.0.0.1:8080/metrics")
    headers = {"Accept": "application/json"}
    if os.getenv("STRATA_API_KEY"):
        headers["Authorization"] = "Bearer " + os.environ["STRATA_API_KEY"]
    while True:
        try:
            with urllib.request.urlopen(urllib.request.Request(endpoint, headers=headers), timeout=5) as response:
                data = json.load(response)
            with LOCK:
                update(data)
                LAST.set(time.time())
                UP.set(1)
        except Exception:
            with LOCK:
                UP.set(0)
                # A failed poll must not leave hardware readings looking live.
                for metric in [*METRICS.values(), TPOT, PREFILL]:
                    metric.clear()
        time.sleep(float(os.getenv("STRATA_POLL_INTERVAL", "3")))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in {"/metrics", "/health"}:
            self.send_error(404)
            return
        with LOCK:
            body = generate_latest(REGISTRY)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    threading.Thread(target=poll, daemon=True).start()
    ThreadingHTTPServer((os.getenv("STRATA_EXPORTER_HOST", "127.0.0.1"), int(os.getenv("STRATA_EXPORTER_PORT", "9402"))), Handler).serve_forever()
