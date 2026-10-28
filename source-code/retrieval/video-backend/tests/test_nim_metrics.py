"""Prometheus parse and live-probe status rules."""
import unittest

from src.utils.nim_metrics import interpret_probe, parse_prometheus

SAMPLE = """
# HELP vllm:num_requests_running Running requests.
# TYPE vllm:num_requests_running gauge
vllm:num_requests_running{model_name="nvidia/cosmos3-nano-reasoner"} 2
vllm:num_requests_running{model_name="other"} 1
vllm:num_requests_waiting{model_name="nvidia/cosmos3-nano-reasoner"} 4
vllm:gpu_cache_usage_perc{model_name="nvidia/cosmos3-nano-reasoner"} 0.91
vllm:kv_cache_usage_perc{model_name="nvidia/cosmos3-nano-reasoner"} 0.42
vllm:e2e_request_latency_seconds_bucket{le="1.0",model_name="nvidia/cosmos3-nano-reasoner"} 3
vllm:e2e_request_latency_seconds_sum{model_name="nvidia/cosmos3-nano-reasoner"} 10
vllm:e2e_request_latency_seconds_count{model_name="nvidia/cosmos3-nano-reasoner"} 4
vllm:prompt_tokens_total{model_name="nvidia/cosmos3-nano-reasoner"} 99999
gpu_memory_used_bytes{device_id="0"} 90
gpu_memory_total_bytes{device_id="0"} 100
gpu_utilization{device_id="0"} 0.25
vllm:time_to_first_token_seconds_sum 4
vllm:time_to_first_token_seconds_count 4
vllm:request_prefill_time_seconds_sum 2
vllm:request_prefill_time_seconds_count 4
vllm:request_decode_time_seconds_sum 8
vllm:request_decode_time_seconds_count 4
vllm:request_success_total{finished_reason="stop",model_name="m"} 10
vllm:request_success_total{finished_reason="length",model_name="m"} 2
vllm:request_success_total{finished_reason="error",model_name="m"} 1
"""


class NimMetricsTests(unittest.TestCase):
    def test_parse_queue_cache_and_latency(self):
        parsed = parse_prometheus(SAMPLE)
        self.assertTrue(parsed["metrics_available"])
        self.assertEqual(parsed["running"], 3)
        self.assertEqual(parsed["waiting"], 4)
        self.assertEqual(parsed["cache_pct"], 42.0)
        self.assertEqual(parsed["avg_latency_sec"], 2.5)
        self.assertEqual(parsed["gpu_memory_pct"], 90.0)
        self.assertEqual(parsed["gpu_util_pct"], 25.0)
        self.assertEqual(parsed["ttft_sec"], 1.0)
        self.assertEqual(parsed["prefill_sec"], 0.5)
        self.assertEqual(parsed["decode_sec"], 2.0)
        self.assertEqual(parsed["finished"], 10)
        self.assertEqual(parsed["cut_off"], 2)
        self.assertEqual(parsed["failed"], 1)
        self.assertNotIn("tokens", parsed)

    def test_cache_above_one_is_already_percent(self):
        parsed = parse_prometheus("gpu_cache_usage_perc 37.5\n")
        self.assertEqual(parsed["cache_pct"], 37.5)

    def test_empty_body(self):
        parsed = parse_prometheus("not prometheus")
        self.assertFalse(parsed["metrics_available"])
        self.assertIsNone(parsed["running"])

    def test_ready_becomes_busy_when_queue_or_cache_is_high(self):
        waiting = interpret_probe(
            name="reasoner",
            label="Reasoner",
            model="m",
            health_code=200,
            error_kind=None,
            parsed={"waiting": 2, "running": 1, "cache_pct": 10, "metrics_available": True},
        )
        self.assertEqual(waiting["status"], "busy")
        hot_cache = interpret_probe(
            name="reasoner",
            label="Reasoner",
            model="m",
            health_code=200,
            error_kind=None,
            parsed={"waiting": 0, "running": 1, "cache_pct": 90, "avg_latency_sec": 1.2, "metrics_available": True},
        )
        self.assertEqual(hot_cache["status"], "busy")
        self.assertEqual(hot_cache["avg_latency_sec"], 1.2)

    def test_timeout_drops_metrics(self):
        result = interpret_probe(
            name="embed",
            label="Embed",
            model="nvidia/cosmos-embed1",
            health_code=None,
            error_kind="timeout",
            parsed={"running": 1, "metrics_available": True},
        )
        self.assertEqual(result["status"], "down")
        self.assertEqual(result["detail"], "Timed out")
        self.assertFalse(result["metrics_available"])
        self.assertIsNone(result["running"])

    def test_missing_health_with_metrics_stays_up(self):
        result = interpret_probe(
            name="embed",
            label="Embed",
            model="nvidia/cosmos-embed1",
            health_code=404,
            error_kind=None,
            parsed={"running": 0, "waiting": 0, "metrics_available": True},
        )
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["metrics_available"])

    def test_health_404_without_metrics_is_unavailable(self):
        result = interpret_probe(
            name="embed",
            label="Embed",
            model="nvidia/cosmos-embed1",
            health_code=404,
            error_kind=None,
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["detail"], "No health endpoint")


if __name__ == "__main__":
    unittest.main()
