import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "litellm"))
import observability_callback as gateway

spec = importlib.util.spec_from_file_location("strata_extra", ROOT / "exporters/strata_extra.py")
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


class StreamSemantics(unittest.TestCase):
    def test_role_and_usage_are_not_output(self):
        for payload in ({"choices": [{"delta": {"role": "assistant"}}]}, {"usage": {"completion_tokens": 8}},
                        {"choices": [{"delta": {}, "finish_reason": "stop"}]}):
            self.assertEqual(gateway.output_delta(payload), (False, False))

    def test_reasoning_and_tools_count_before_visible_answer(self):
        self.assertEqual(gateway.output_delta({"choices": [{"delta": {"reasoning_content": "think"}}]}), (True, False))
        self.assertEqual(gateway.output_delta({"choices": [{"delta": {"tool_calls": [{"function": {"arguments": "17"}}]}}]}), (True, False))
        self.assertEqual(gateway.output_delta({"choices": [{"delta": {"content": "42"}}]}), (True, True))

    def test_tpot_never_uses_chunk_count_as_token_count(self):
        self.assertAlmostEqual(gateway.tpot_value(1., 3., 101, 2, True)[0], .02)
        for args, reason in [((1., 3., None, 2, True), "missing_usage"), ((1., 3., 1, 2, True), "too_few_tokens"),
                             ((1., 3., 100, 1, True), "coalesced_or_no_output"), ((1., 3., 100, 2, False), "non_stream")]:
            self.assertEqual(gateway.tpot_value(*args), (None, reason))

    def test_usage_reconciles_reasoning_and_cached_components(self):
        raw, usage, tokens = gateway.normalize_usage({"usage": {"prompt_tokens": 100, "completion_tokens": 20,
            "prompt_tokens_details": {"cached_tokens": 75}, "completion_tokens_details": {"reasoning_tokens": 12}}})
        self.assertEqual(tokens, 20)
        self.assertEqual(usage["total"], 120)
        self.assertEqual(sum(usage[k] for k in ("input", "input_cached_tokens", "output", "output_reasoning_tokens")), 120)
        self.assertEqual(raw["prompt_tokens"], 100)

    def test_missing_usage_breakdown_is_not_reported_as_measured_zero(self):
        _, details, _ = gateway.normalize_usage({"usage": {"prompt_tokens": 10, "completion_tokens": 20}})
        self.assertNotIn("output_reasoning_tokens", details)
        self.assertNotIn("input_cached_tokens", details)


class TraceIntegration(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.logger = gateway.GatewayObservability(source_usage=gateway.SourceUsageCollector(":memory:"))
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider()
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.logger.provider = self.provider
        self.logger.tracer = self.provider.get_tracer("test")

    def tearDown(self):
        self.provider.shutdown()

    async def test_parent_propagation_tool_payload_and_secret_exclusion(self):
        tid, pid = "1" * 32, "2" * 16
        data = {"model": "qwen-strata", "litellm_call_id": "test-parent", "messages": [{"role": "user", "content": "17+25"}],
                "metadata": {"session_id": "session-A", "trace_user_id": "user-A", "api_key": "sk-must-not-export"},
                "proxy_server_request": {"headers": {"traceparent": f"00-{tid}-{pid}-01", "authorization": "Bearer sk-must-not-export"}}}
        await self.logger.async_pre_call_hook(None, None, data, "completion")
        kwargs = {"model": "upstream-id", "litellm_call_id": data["litellm_call_id"], "litellm_params": {"metadata": data["metadata"]}}
        response = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call-1", "type": "function", "function": {"name": "add_numbers", "arguments": '{"a":17,"b":25}'}}]}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 20}}
        await self.logger.async_log_success_event(kwargs, response, None, None)
        # LiteLLM can report a terminal event twice; keep a single generation.
        await self.logger.async_log_success_event(kwargs, response, None, None)
        spans = self.exporter.get_finished_spans()
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(format(span.context.trace_id, "032x"), tid)
        self.assertEqual(format(span.parent.span_id, "016x"), pid)
        self.assertEqual(span.attributes["session.id"], "session-A")
        self.assertEqual(span.attributes["user.id"], "user-A")
        self.assertIn("add_numbers", span.attributes["langfuse.observation.output"])
        self.assertNotIn("sk-must-not-export", json.dumps(dict(span.attributes)))
        self.assertFalse(self.logger.states)

    async def test_verified_source_survives_export_and_usage_counts_once(self):
        data = {"model": "source-model", "litellm_call_id": "source-request",
                "metadata": {"requester_ip_address": "172.18.0.10", "source_ip": "192.0.2.99"},
                "proxy_server_request": {"headers": {"X-Forwarded-For": "198.51.100.7", "Authorization": "Bearer private"}}}
        with patch.dict("os.environ", {"GATEWAY_TRUSTED_PROXY_CIDRS": "172.18.0.10/32"}):
            await self.logger.async_pre_call_hook(None, None, data, "completion")
        response = {"choices": [{"message": {"content": "42"}}], "usage": {"prompt_tokens": 30, "completion_tokens": 20,
                    "prompt_tokens_details": {"cached_tokens": 5}, "completion_tokens_details": {"reasoning_tokens": 12}}}
        await self.logger.async_log_success_event(data, response, None, None)
        await self.logger.async_log_success_event(data, response, None, None)
        span = self.exporter.get_finished_spans()[0]
        metadata = json.loads(span.attributes["langfuse.observation.metadata"])
        self.assertEqual(metadata["source_ip"], "198.51.100.7")
        self.assertEqual(metadata["network_peer_ip"], "172.18.0.10")
        self.assertEqual(json.loads(span.attributes["langfuse.trace.metadata"])["source_ip"], "198.51.100.7")
        self.assertNotIn("Bearer private", json.dumps(dict(span.attributes)))
        samples = {tuple(sorted(sample.labels.items())): sample.value for family in self.logger.source_usage.collect()
                   for sample in family.samples if sample.name == "llm_gateway_source_tokens_total"}
        for token_type, expected in [("input", 30), ("output", 20), ("input_cached", 5), ("output_reasoning", 12)]:
            self.assertEqual(samples[tuple(sorted({"source_ip": "198.51.100.7", "model": "source-model", "token_type": token_type}.items()))], expected)

    async def test_concurrent_streams_do_not_share_timestamps(self):
        requests = []
        for key in ("A", "B"):
            data = {"model": "qwen-strata", "litellm_call_id": key, "stream": True}
            await self.logger.async_pre_call_hook(None, None, data, "completion")
            requests.append(data)

        async def chunks(text):
            yield {"choices": [{"delta": {"role": "assistant"}}]}
            await asyncio.sleep(0)
            yield {"choices": [{"delta": {"reasoning_content": text}}]}
            await asyncio.sleep(0)
            yield {"choices": [{"delta": {"content": text}}]}

        async def consume(data):
            return [x async for x in self.logger.async_post_call_streaming_iterator_hook(None, chunks(data["litellm_call_id"]), data)]

        await asyncio.gather(*(consume(data) for data in requests))
        a, b = (self.logger._state(data) for data in requests)
        self.assertIsNot(a, b)
        self.assertEqual((a.frames, b.frames), (2, 2))
        self.assertLess(a.first, a.answer)
        self.assertLess(b.first, b.answer)

    async def test_langfuse_datetime_encoding_and_error_level(self):
        data = {"model": "qwen-strata", "litellm_call_id": "datetime", "stream": True}
        await self.logger.async_pre_call_hook(None, None, data, "completion")
        state = self.logger._state(data)
        state.first, state.last, state.frames = state.started + .1, state.started + .4, 3
        kwargs = dict(data, exception=RuntimeError("Bearer sk-do-not-print"))
        await self.logger.async_log_failure_event(kwargs, {}, None, None)
        span = self.exporter.get_finished_spans()[0]
        from datetime import datetime
        datetime.fromisoformat(json.loads(span.attributes["langfuse.observation.completion_start_time"]))
        self.assertEqual(span.attributes["langfuse.observation.level"], "ERROR")
        self.assertNotIn("sk-do-not-print", span.attributes["langfuse.observation.status_message"])

    async def test_interrupted_stream_is_a_single_error_observation(self):
        data = {"model": "qwen-strata", "litellm_call_id": "interrupted", "stream": True,
                "messages": [{"role": "user", "content": "count"}]}
        await self.logger.async_pre_call_hook(None, None, data, "completion")

        async def broken_stream():
            yield {"choices": [{"delta": {"content": "1"}}]}
            raise ConnectionError("stream closed")

        with self.assertRaises(ConnectionError):
            async for _ in self.logger.async_post_call_streaming_iterator_hook(None, broken_stream(), data):
                pass
        # A subsequent LiteLLM failure callback must not create a duplicate.
        await self.logger.async_log_failure_event(data, {}, None, None)
        spans = self.exporter.get_finished_spans()
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].attributes["langfuse.observation.level"], "ERROR")
        metadata = json.loads(spans[0].attributes["langfuse.observation.metadata"])
        self.assertEqual(metadata["tpot_excluded_reason"], "failed")
        self.assertIsNone(metadata["tpot_seconds"])
        self.assertFalse(self.logger.states)


class StrataTelemetry(unittest.TestCase):
    def test_binary_units_missing_fields_and_model_reload(self):
        exporter.update({"engine": {"model": "old-model"}, "hardware": {"disk_read_mb": 1, "gpu_pcie_rx_mb": 2},
                         "requests": [{"prompt_tokens": 100, "reused": 60, "prompt_ms": 20, "output_tokens": 5, "decode_ms": 80}]})
        self.assertEqual(exporter.REGISTRY.get_sample_value("strata_extra_disk_read_bytes_per_second", {"model": "old-model"}), 1048576)
        self.assertEqual(exporter.REGISTRY.get_sample_value("strata_extra_last_request_prefill_tokens_per_second", {"model": "old-model"}), 2000)
        self.assertAlmostEqual(exporter.REGISTRY.get_sample_value("strata_extra_last_request_engine_tpot_seconds", {"model": "old-model"}), .02)
        exporter.update({"engine": {"model": "new-model"}, "hardware": {}, "requests": []})
        self.assertIsNone(exporter.REGISTRY.get_sample_value("strata_extra_disk_read_bytes_per_second", {"model": "old-model"}))
        self.assertIsNone(exporter.REGISTRY.get_sample_value("strata_extra_disk_read_bytes_per_second", {"model": "new-model"}))


class SourceAccounting(unittest.TestCase):
    def test_untrusted_forwarding_and_request_metadata_cannot_choose_source(self):
        source = gateway.source_address({"requester_ip_address": "127.0.0.1", "source_ip": "198.51.100.99"},
                                        {"x-forwarded-for": "198.51.100.8", "x-real-ip": "198.51.100.9"}, "172.18.0.10/32")
        self.assertEqual(source["source_ip"], "127.0.0.1")
        self.assertEqual(source["source_ip_origin"], "transport_peer")
        self.assertEqual(gateway.source_address({}, {"x-forwarded-for": "198.51.100.8"}, "")["source_ip"], "unknown")

    def test_proxy_chain_uses_nearest_untrusted_hop_and_rejects_malformed_values(self):
        peer = {"requester_ip_address": "::ffff:172.18.0.10"}
        self.assertEqual(gateway.source_address(peer, {"x-forwarded-for": "192.0.2.9, 198.51.100.8, 172.18.0.11"},
                                                "172.18.0.0/24")["source_ip"], "198.51.100.8")
        self.assertEqual(gateway.source_address(peer, {"x-forwarded-for": "invalid, 198.51.100.8"},
                                                "172.18.0.0/24")["source_ip"], "172.18.0.10")
        self.assertEqual(gateway.canonical_ip("2001:0db8::1"), "2001:db8::1")

    def test_durable_counts_overflow_and_missing_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "usage.sqlite3")
            collector = gateway.SourceUsageCollector(path, max_series=1)
            collector.record("first", "198.51.100.7", "m", {"input_tokens": 30, "output_tokens": 20})
            collector.record("first", "198.51.100.7", "m", {"input_tokens": 30, "output_tokens": 20})
            collector.connection.close()
            restored = gateway.SourceUsageCollector(path, max_series=1)
            restored.record("first", "198.51.100.7", "m", {"input_tokens": 30, "output_tokens": 20})
            restored.record("second", "198.51.100.8", "m", {}, failed=True)
            rows = restored._db().execute("SELECT source_ip, model, input, output, success, error, missing FROM source_totals ORDER BY source_ip").fetchall()
            self.assertEqual(rows, [("198.51.100.7", "m", 30, 20, 1, 0, 0), ("overflow", "all", 0, 0, 0, 1, 1)])
            restored.connection.close()


if __name__ == "__main__":
    unittest.main()
