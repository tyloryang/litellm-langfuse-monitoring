#!/usr/bin/env python3
"""Validate the reused WSL stack, dashboard PromQL, and optional real requests."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def request(url, headers=None, body=None, timeout=20):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def raw_usage_from_metadata(metadata):
    raw = metadata.get("usage_raw")
    if isinstance(raw, dict):
        return raw
    # Langfuse's persisted metadata may be flattened into dotted keys after
    # enrichment, even when the just-ingested response contained a JSON object.
    result = {}
    for key, value in metadata.items():
        if not key.startswith("usage_raw."):
            continue
        parts = key[len("usage_raw."):].split(".")
        target = result
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value
    return result or None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path.home() / "litellm-langfuse-monitoring/.env")
    parser.add_argument("--prometheus", default="http://127.0.0.1:9090")
    parser.add_argument("--gateway", default="http://127.0.0.1:4000")
    parser.add_argument("--langfuse", default="http://127.0.0.1:3001")
    parser.add_argument("--exercise", action="store_true", help="Run one small real streaming request")
    parser.add_argument("--trace-id", help="Validate an existing real agent/tool trace")
    parser.add_argument("--stream-trace-id", help="Validate a previously ingested streaming generation")
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime/validation.json")
    args = parser.parse_args()
    env = dotenv_values(args.env_file)
    report = {"services": [], "targets": [], "queries": [], "failures": []}
    for name, url, headers in (
        ("gateway", args.gateway + "/health/readiness", {"Authorization": "Bearer " + env["LITELLM_MASTER_KEY"]}),
        ("langfuse", args.langfuse + "/api/public/health", {}),
    ):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=10) as response:
                report["services"].append({"service": name, "status": response.status})
        except Exception as error:
            report["services"].append({"service": name, "error": str(error)})
            report["failures"].append("Service unhealthy: " + name)
    targets = request(args.prometheus + "/api/v1/targets")["data"]["activeTargets"]
    for target in targets:
        job = target["labels"]["job"]
        report["targets"].append({"job": job, "health": target["health"], "error": target["lastError"]})
        if job in {"litellm", "strata", "strata-extra"} and target["health"] != "up":
            report["failures"].append(f"Target {job}: {target['lastError']}")
    for job in {"litellm", "strata", "strata-extra"} - {t["labels"]["job"] for t in targets}:
        report["failures"].append("Missing target " + job)
    dashboard = json.loads((ROOT / "grafana/dashboards/litellm-gpu-overview.json").read_text(encoding="utf-8"))
    queries = [(p["title"], t["expr"]) for p in dashboard["panels"] for t in p.get("targets", [])]

    def query(item):
        title, expr = item
        expr = expr.replace("$__rate_interval", "1m").replace("$__range", "1h")
        for name in ("gateway_model", "engine_model", "deployment", "gpu"):
            expr = expr.replace("$" + name, ".*")
        try:
            result = request(args.prometheus + "/api/v1/query?" + urllib.parse.urlencode({"query": expr}))
            rows = result.get("data", {}).get("result", [])
            return {"panel": title, "ok": result["status"] == "success", "series": len(rows),
                    "finite_series": sum(row.get("value", [None, "NaN"])[1] not in {"NaN", "+Inf", "-Inf"} for row in rows)}
        except Exception as error:
            return {"panel": title, "ok": False, "error": str(error)}

    with ThreadPoolExecutor(max_workers=6) as pool:
        report["queries"] = list(pool.map(query, queries))
    report["failures"].extend("PromQL: " + q["panel"] for q in report["queries"] if not q["ok"])
    critical = ["GPU 利用率", "显存占用率", "最近 TTFT · 引擎", "最近 TPOT · 引擎均值", "请求运行 / 排队", "会话缓存容量 / 预算"]
    for title in critical:
        if not any(q["panel"] == title and q.get("finite_series", 0) > 0 for q in report["queries"]):
            report["failures"].append("No finite data: " + title)
    auth = {"Authorization": "Basic " + base64.b64encode(f"{env['LANGFUSE_PUBLIC_KEY']}:{env['LANGFUSE_SECRET_KEY']}".encode()).decode()}

    def observations(**filters):
        return request(args.langfuse + "/api/public/v2/observations?" + urllib.parse.urlencode(
            # v4 omits timing/computed fields unless explicitly selected. A
            # missing field in a partial API response is not missing telemetry.
            {"limit": 100, "fields": "core,basic,time,metrics,io,usage,metadata,model", **filters}), auth)["data"]

    if args.trace_id:
        rows = [o for o in observations(traceId=args.trace_id) if o["traceId"] == args.trace_id]
        types = [o["type"] for o in rows]
        agents = {o["id"] for o in rows if o["type"] == "AGENT"}
        tool = next((o for o in rows if o["type"] == "TOOL" and o["name"] == "add_numbers"), None)
        good = len(agents) == 1 and types.count("GENERATION") == 2 and tool is not None and all(
            o["parentObservationId"] in agents for o in rows if o["type"] != "AGENT")
        good = good and len({o.get("sessionId") for o in rows}) == 1 and all(o.get("sessionId") and o.get("userId") for o in rows)
        good = good and bool(tool) and json.loads(tool["output"]).get("result") == 42
        report["tool_chain"] = {"trace_id": args.trace_id, "types": types, "parent_links_and_session": bool(good)}
        if not good:
            report["failures"].append("Tool trace did not contain a complete, consistently parented real execution chain")
    if args.exercise:
        session = "monitoring-stream-" + uuid.uuid4().hex[:12]
        body = {"model": "qwen-strata", "stream": True, "stream_options": {"include_usage": True},
                "messages": [{"role": "user", "content": "Reply with the numbers 1 to 12, separated by spaces. No explanation."}],
                "max_tokens": 128, "temperature": 0,
                "metadata": {"session_id": session, "trace_user_id": "monitoring-validation", "trace_name": "stream-validation"}}
        req = urllib.request.Request(args.gateway + "/v1/chat/completions", data=json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + env["LITELLM_MASTER_KEY"], "Content-Type": "application/json"})
        usage = None
        frames = 0
        with urllib.request.urlopen(req, timeout=240) as response:
            for line in response:
                if not line.startswith(b"data: ") or line[6:].strip() == b"[DONE]":
                    continue
                event = json.loads(line[6:])
                frames += 1
                if event.get("usage"):
                    usage = event["usage"]
        found = []
        for attempt in range(30):
            found = [o for o in observations(sessionId=session) if o.get("sessionId") == session and o["type"] == "GENERATION"]
            if found:
                break
            time.sleep(1)
        if not found:
            report["failures"].append("Streaming generation was not ingested by Langfuse")
    elif args.stream_trace_id:
        found = [o for o in observations(traceId=args.stream_trace_id)
                 if o["traceId"] == args.stream_trace_id and o["type"] == "GENERATION"]
        usage = raw_usage_from_metadata(found[0].get("metadata") or {}) if found else None
        frames = None
        if not found:
            report["failures"].append("Streaming generation was not found")
    if (args.exercise or args.stream_trace_id) and found:
        item = found[0]
        metadata = item.get("metadata") or {}
        ttft = item.get("timeToFirstToken")
        details = item.get("usageDetails") or {}
        component_total = sum(details.get(key, 0) for key in
                              ("input", "input_cached_tokens", "output", "output_reasoning_tokens"))
        valid = (ttft is not None and ttft > 0 and
                 abs(ttft - (metadata.get("ttft_seconds") or 0)) < .005 and
                 (metadata.get("tpot_seconds") or 0) > 0 and usage is not None and
                 details.get("total") == usage.get("total_tokens") == component_total and
                 bool(item.get("input")) and bool(item.get("output")))
        report["stream"] = {"trace_id": item["traceId"], "sse_frames": frames,
                            "meaningful_output_frames": metadata.get("stream_output_frames"), "usage": usage,
                            "langfuse_usage_details": details,
                            "langfuse_ttft_seconds": ttft,
                            "tpot_seconds": metadata.get("tpot_seconds"), "validated": bool(valid)}
        if not valid:
            report["failures"].append("Streaming TTFT / TPOT / usage did not reconcile")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"services": report["services"], "targets": report["targets"], "queries": len(report["queries"]),
                      "queries_with_data": sum(q.get("finite_series", 0) > 0 for q in report["queries"]),
                      "tool_chain": report.get("tool_chain"), "stream": report.get("stream"),
                      "failures": report["failures"], "report": str(args.output)}, ensure_ascii=False, indent=2))
    if report["failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
