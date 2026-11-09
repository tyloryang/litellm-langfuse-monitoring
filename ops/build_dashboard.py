#!/usr/bin/env python3
"""Generate the importable dashboard; keep queries and descriptions reviewable."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DS = {"type": "prometheus", "uid": "${DS_PROMETHEUS}"}
panels = []
y = 0


def legend(expression):
    percentile = re.search(r"histogram_quantile\(([\d.]+)", expression)
    if percentile:
        return "P" + str(round(float(percentile[1]) * 100))
    if 'sum by (reason)' in expression:
        return "{{reason}}"
    if 'strata:live_slots' in expression:
        return "{{state}}"
    if expression.startswith('up{'):
        return "{{job}} {{instance}}"
    if 'strata_extra_up' in expression:
        return "JSON poll UP"
    if 'last_success_timestamp' in expression:
        return "数据年龄 (s)"
    labels = "{{requested_model}}" if "requested_model" in expression else "{{model}}"
    if 'strata:' in expression:
        labels = "{{model_name}}"
    if 'llm:gpu_' in expression:
        labels = "{{deployment}} GPU {{gpu}}"
    for part, name in (("memory_used", "已用"), ("mem_used", "已用"), ("ram_used", "已用"),
                       ("memory_total", "总量"), ("mem_total", "总量"), ("ram_total", "总量"),
                       ("live_tok_s", "Decode"), ("live_prefill", "Prefill"),
                       ("running", "运行"), ("waiting", "排队"), ("input_tokens", "输入"),
                       ("output_tokens", "输出"), ("cached_tokens", "缓存读取"), ("reasoning_tokens", "推理"),
                       ("_rx_", "接收"), ("_tx_", "发送"), ("disk_read", "读取"), ("disk_write", "写入"),
                       ("power_limit", "功率上限"), ("power_watts", "功耗"), ("budget_bytes", "预算"),
                       ("cache_bytes", "已用"), ("restores", "恢复"), ("evictions", "驱逐")):
        if part in expression:
            return name + " · " + labels
    return labels


def row(title):
    global y
    panels.append({"id": len(panels) + 1, "title": title, "type": "row", "collapsed": False,
                   "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []})
    y += 1


def panel(title, expressions, unit="short", description="", kind="timeseries", width=8, x=0,
          height=7, thresholds=None, legends=None):
    expressions = [expressions] if isinstance(expressions, str) else expressions
    default = {"unit": unit, "decimals": 2, "noValue": "暂无样本", "color": {"mode": "palette-classic"},
               "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None},
                                {"color": "orange", "value": 80}, {"color": "red", "value": 95}]}}
    if thresholds:
        default["thresholds"]["steps"] = [{"color": "green", "value": None}] + [{"color": c, "value": v} for v, c in thresholds]
    if unit == "percent":
        default.update(min=0, max=100)
    if unit == "percentunit":
        default.update(min=0, max=1)
    if kind == "timeseries":
        default["custom"] = {"drawStyle": "line", "lineInterpolation": "smooth", "lineWidth": 2,
                             "fillOpacity": 10, "spanNulls": False, "showPoints": "never", "axisLabel": "",
                             "hideFrom": {"tooltip": False, "viz": False, "legend": False}}
    options = ({"legend": {"displayMode": "table", "placement": "bottom", "calcs": ["lastNotNull", "max"]},
                "tooltip": {"mode": "multi", "sort": "desc"}}
               if kind == "timeseries" else
               {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                "orientation": "auto", "textMode": "value_and_name", "colorMode": "value",
                "graphMode": "area", "justifyMode": "auto"})
    panels.append({"id": len(panels) + 1, "title": title, "description": description, "type": kind,
                   "gridPos": {"x": x, "y": y, "w": width, "h": height}, "datasource": DS,
                   "fieldConfig": {"defaults": default, "overrides": []}, "options": options,
                   "targets": [{"refId": chr(65 + i), "expr": expr, "legendFormat": legends[i] if legends else legend(expr),
                                "instant": kind == "stat", "range": kind != "stat", "datasource": DS} for i, expr in enumerate(expressions)]})


def band(items, width=8, height=7, kind="timeseries"):
    global y
    for i, item in enumerate(items):
        panel(*item, kind=kind, width=width, x=(i % (24 // width)) * width, height=height)
        if (i + 1) % (24 // width) == 0:
            y += height
    if len(items) % (24 // width):
        y += height


G = '{requested_model=~"$gateway_model"}'
M = '{model=~"$engine_model"}'
N = '{model_name=~"$engine_model",job="strata"}'
H = '{deployment=~"$deployment",gpu=~"$gpu"}'
rate = lambda name, selector="": f"rate({name}{selector}[$__rate_interval])"
quantile = lambda name, selector, q: f"histogram_quantile({q}, sum by (le) ({rate(name + '_bucket', selector)}))"

row("01 · 实时总览 / 与 Strata Monitor 对齐")
band([
    ("网关可用性", 'min(up{job="litellm"})', "short", "1=Prometheus 能抓取网关。",),
    ("解码速度 · 最近请求", f"strata:last_decode_tok_s{N}", "suffix:tok/s", "最近完成请求的引擎解码速度；空闲时保留最近值。"),
    ("GPU 利用率", f"llm:gpu_utilization_percent{H}", "percent", "来自 Strata 实际硬件采样；DCGM 部署可复用相同面板。"),
    ("显存占用率", f"100 * llm:gpu_memory_used_bytes{H} / llm:gpu_memory_total_bytes{H}", "percent", "驻留模型也占显存，空闲时显存通常不会清零。"),
    ("最近 TTFT · 引擎", f"strata_extra_last_request_ttft_seconds{M}", "s", "引擎首次生成事件，和网关到达客户端的口径不同。"),
    ("最近 TPOT · 引擎均值", f"strata_extra_last_request_engine_tpot_seconds{M}", "s", "decode_ms / 1000 / (output_tokens - 1)，包含推理 Token；不是逐 Token ITL。"),
], width=4, height=5, kind="stat")
band([
    ("实时解码 / Prefill 速度", [f"strata:live_tok_s{N}", f"strata:live_prefill_tok_s_mean{N}"], "suffix:tok/s", "直接对应 Strata Monitor Speed / Prefill；空闲为 0。"),
    ("GPU / 显存占用趋势", [f"llm:gpu_utilization_percent{H}", f"100 * llm:gpu_memory_used_bytes{H} / llm:gpu_memory_total_bytes{H}"], "percent"),
    ("请求运行 / 排队", [f"llm:engine_requests_running{M}", f"llm:engine_requests_waiting{M}"], "short"),
])
row("02 · 首输出、每 Token 时间与请求延迟")
band([
    ("TTFT · 网关 P50 / P95 / P99", [quantile("llm_gateway_ttft_seconds", '{model=~"$gateway_model"}', q) for q in (.5, .95, .99)], "s", "从网关接收到第一个有效内容、推理或工具增量。仅统计流式。"),
    ("TPOT · 网关请求均值 P50 / P95 / P99", [quantile("llm_gateway_tpot_seconds", '{model=~"$gateway_model"}', q) for q in (.5, .95, .99)], "s", "每请求 (last-first)/(completion_tokens-1)。仅成功、usage 完整且至少两帧有效输出的流式请求；网络合并帧会影响估计。"),
    ("首段可见答案 · P50 / P95", [quantile("llm_gateway_first_answer_seconds", '{model=~"$gateway_model"}', q) for q in (.5, .95)], "s", "排除纯推理及工具增量，长思考模型的该值通常高于 TTFT。"),
    ("TTFT · 引擎 P50 / P95", [quantile("llm:engine_ttft_seconds", M, q) for q in (.5, .95)], "s"),
    ("ITL · 引擎 P50 / P95", [quantile("llm:engine_itl_seconds", M, q) for q in (.5, .95)], "s", "vLLM/SGLang 的引擎 ITL；Strata 当前由 decode 平均间隔按 Token 间隔数加权估算。不要当作请求级 TPOT。"),
    ("端到端延迟 · P50 / P95 / P99", [quantile("llm_gateway_e2e_seconds", '{model=~"$gateway_model"}', q) for q in (.5, .95, .99)], "s"),
])
band([
    ("LiteLLM 上游 API 延迟 P95", quantile("litellm_llm_api_latency_metric", G, .95), "s"),
    ("LiteLLM 网关额外开销 P95", quantile("litellm_overhead_latency_metric", G, .95), "s"),
    ("TPOT 被排除的请求", 'sum by (reason) (increase(llm_gateway_tpot_skipped_total{model=~"$gateway_model"}[$__range]))', "short", "非流式、少 Token、usage 缺失、帧合并及失败分别计数。"),
])
row("03 · Token、吞吐和请求质量")
band([
    ("输入 Token / s · 网关", f"llm:gateway_input_tokens_per_second{G}", "suffix:tok/s"),
    ("输出 Token / s · 网关", f"llm:gateway_output_tokens_per_second{G}", "suffix:tok/s"),
    ("输入 / 输出 Token · 引擎", [f"llm:engine_input_tokens_per_second{M}", f"llm:engine_output_tokens_per_second{M}"], "suffix:tok/s", "引擎包含绕过 LiteLLM 的直接请求，因此数值可能与网关不同。"),
    ("请求 QPS", f"llm:gateway_requests_per_second{G}", "reqps"),
    ("失败率", f"100 * (sum({rate('litellm_proxy_failed_requests_metric_total', G)}) or vector(0)) / clamp_min(sum({rate('litellm_proxy_total_requests_metric_total', G)}), 0.000001)", "percent", "零失败可显示 0；没有请求时不生成虚假的延迟样本。"),
    ("网关并发请求", "sum(litellm_in_flight_requests)", "short", "当前 LiteLLM gauge 无 requested_model 标签，为整个网关并发。"),
])
band([
    ("期间输入 / 输出 Token", [f"sum(increase(litellm_input_tokens_metric_total{G}[$__range]))", f"sum(increase(litellm_output_tokens_metric_total{G}[$__range]))"], "short"),
    ("缓存读取 / 推理 Token 速率", [f"sum({rate('litellm_input_cached_tokens_metric_total', G)})", f"sum({rate('litellm_output_reasoning_tokens_metric_total', G)})"], "suffix:tok/s", "是否提供细分 usage 取决于上游；无字段时不伪造数据。"),
    ("工具调用提议 / s", 'sum by (model) (rate(llm_gateway_tool_proposals_total{model=~"$gateway_model"}[$__rate_interval]))', "ops", "模型返回的工具调用提议，不代表工具已经执行。执行结果到 Langfuse 工具 span 查看。"),
])
row("04 · Strata 缓存、上下文和 MTP")
band([
    ("前缀缓存命中率 · Token 加权", f"sum({rate('vllm:prefix_cache_hits_total', N)}) / clamp_min(sum({rate('vllm:prefix_cache_queries_total', N)}), 0.000001)", "percentunit"),
    ("专家 VRAM 缓存命中率 · 最近请求", f"strata:last_hit_rate{N}", "percentunit", "与 Strata VRAM hit rate 相同，不是前缀缓存或物理 KV 占用。"),
    ("MTP 草稿接受率", f"sum({rate('vllm:spec_decode_num_accepted_tokens_total', N)}) / clamp_min(sum({rate('vllm:spec_decode_num_draft_tokens_total', N)}), 0.000001)", "percentunit"),
    ("Strata 上下文占用", f"llm:strata_context_usage_ratio{M}", "percentunit", "运行中 prompt+generated / max_context；空闲为 0。不是物理 KV 显存利用率。"),
    ("最大上下文 / GPU 驻留 KV 位置", [f"strata:engine_max_context{N}", f"strata_extra_kv_resident_tokens{M}"], "short", "两者单位都为 Token 位置，驻留 KV 为每层驻留位置。"),
    ("并行槽位状态", f"strata:live_slots{N}", "short", "idle / reading / generating。"),
    ("会话缓存容量 / 预算", [f"strata_extra_conversation_cache_bytes{M}", f"strata_extra_conversation_cache_budget_bytes{M}"], "bytes"),
    ("会话缓存恢复 / 驱逐速率", [rate("strata_extra_conversation_cache_restores_total", M), rate("strata_extra_conversation_cache_evictions_total", M)], "ops"),
    ("专家缓存大小", f"strata_extra_expert_cache_bytes{M}", "bytes"),
])
row("05 · GPU 与主机硬件 / Strata Monitor")
band([
    ("GPU 显存 已用 / 总量", [f"llm:gpu_memory_used_bytes{H}", f"llm:gpu_memory_total_bytes{H}"], "bytes"),
    ("GPU 温度", f"llm:gpu_temperature_celsius{H}", "celsius"),
    ("GPU 功耗 / 功率上限", [f"llm:gpu_power_watts{H}", f"strata_extra_gpu_power_limit_watts{M}"], "watt"),
    ("PCIe 接收 / 发送", [f"strata_extra_gpu_pcie_rx_bytes_per_second{M}", f"strata_extra_gpu_pcie_tx_bytes_per_second{M}"], "Bps"),
    ("CPU 使用率", f"strata:cpu{N}", "percent"),
    ("主机 RAM 已用 / 总量", [f"strata:ram_used_bytes{N}", f"strata:ram_total_bytes{N}"], "bytes"),
    ("磁盘读 / 写速率", [f"strata_extra_disk_read_bytes_per_second{M}", f"strata_extra_disk_write_bytes_per_second{M}"], "Bps"),
    ("最近请求 Prefill 吞吐", f"strata_extra_last_request_prefill_tokens_per_second{M}", "suffix:tok/s", "(prompt_tokens-reused)/prompt_seconds，与 Strata 请求表计算一致。"),
    ("最近请求输入 / 输出 / 复用 Token", [f"strata_extra_last_request_prompt_tokens{M}", f"strata_extra_last_request_output_tokens{M}", f"strata_extra_last_request_reused_tokens{M}"], "short"),
])
row("06 · 采集健康、计费配置及其他引擎")
band([
    ("采集目标 UP", 'up{job=~"litellm|strata|strata-extra|vllm|sglang|gpu-dcgm"}', "short", "0=采集异常；不存在的部署不会伪装成正常。"),
    ("Strata JSON 采集健康 / 数据年龄", ["strata_extra_up", "time() - strata_extra_last_success_timestamp_seconds"], "short", "健康应为 1；数据年龄应小于两个轮询周期。"),
    ("Langfuse 上报失败 / 本地构造错误", ['increase(llm_gateway_otel_exports_total{status="failure"}[$__range])', "increase(llm_gateway_observation_errors_total[$__range])"], "short", "分别追踪远程 OTLP HTTP 导出失败与本地构造错误；异步队列的超量丢弃需结合网关日志。"),
    ("未配置价格的请求", 'sum(increase(llm_gateway_unpriced_requests_total{model=~"$gateway_model"}[$__range]))', "short", "本地模型无官方单价时标为未配置。应先设置真实每 Token 成本，再解释费用为零。"),
    ("LiteLLM 费用 · USD", f"sum(increase(litellm_spend_metric_total{G}[$__range]))", "currencyUSD", "需要单独配置 LiteLLM model_info 的价格，未配置时 0 不代表免费。"),
    ("vLLM / SGLang KV Cache 占用", f"llm:engine_kv_usage_ratio{M}", "percentunit", "本机仅部署 Strata 时该面板无样本；Strata 上下文占用在单独面板。"),
])


def variable(name, label, query):
    return {"name": name, "label": label, "type": "query", "datasource": DS, "definition": query,
            "query": {"query": query, "refId": name}, "refresh": 1, "includeAll": True, "multi": True,
            "allValue": ".*", "current": {"text": "All", "value": "$__all"}, "options": [], "sort": 1}


dashboard = {"id": None, "uid": "litellm-gpu-overview", "title": "LLM 推理观测 · Strata / LiteLLM / GPU",
             "description": "Strata Monitor 同源指标、网关 TTFT/TPOT、GPU、缓存与 Langfuse 数据质量。详见各面板说明。",
             "tags": ["LLM", "Strata", "LiteLLM", "GPU", "Langfuse"], "timezone": "browser", "schemaVersion": 39,
             "version": 2, "editable": True, "graphTooltip": 1, "refresh": "10s", "time": {"from": "now-1h", "to": "now"},
             "annotations": {"list": []}, "links": [], "panels": panels,
             "templating": {"list": [{"name": "DS_PROMETHEUS", "label": "数据源", "type": "datasource", "query": "prometheus",
                                      "current": {}, "options": [], "refresh": 1},
                                     variable("gateway_model", "网关模型", "label_values(litellm_proxy_total_requests_metric_total, requested_model)"),
                                     variable("engine_model", "引擎模型", "label_values(llm:engine_requests_running, model)"),
                                     variable("deployment", "部署", "label_values(llm:gpu_utilization_percent, deployment)"),
                                     variable("gpu", "GPU", 'label_values(llm:gpu_utilization_percent{deployment=~"$deployment"}, gpu)')]}}

if __name__ == "__main__":
    output = ROOT / "grafana/dashboards/litellm-gpu-overview.json"
    output.write_text(json.dumps(dashboard, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Generated {len([p for p in panels if p['type'] != 'row'])} panels: {output}")
