import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from workflow.provider import DeepSeekProvider, DemoProvider, ProviderError, make_provider


class DemoProviderTests(unittest.TestCase):
    def test_demo_mode_never_requests_network_and_retains_goal(self):
        with patch("workflow.provider._open_request", side_effect=AssertionError("Unexpected network")):
            result = DemoProvider().generate("product", "整理任务", {"kind": "brief", "goal": "课程签到管理"}, "economy")
        self.assertEqual(result["data"]["summary"], "课程签到管理")
        tasks = result["data"]["tasks"]
        self.assertEqual(len({task["id"] for task in tasks}), len(tasks))
        self.assertFalse(result["usage"]["billed"])
        self.assertEqual(result["usage"]["estimated_cost_usd"], 0.0)

    def test_first_challenge_creates_a_linked_blocker_then_review_can_converge(self):
        provider = DemoProvider()
        payload = {"kind": "critique", "claims": [{"id": "C-1"}]}
        first = provider.generate("challenger", "检查异议", {**payload, "round": 1}, "strong")["data"]
        later = provider.generate("challenger", "检查异议", {**payload, "round": 2}, "strong")["data"]
        self.assertEqual(first["stance"], "conditional")
        self.assertEqual(first["objections"][0]["claim_id"], "C-1")
        self.assertTrue(first["objections"][0]["blocking"])
        self.assertEqual(later, {"stance": "support", "rationale": "当前观点保留已确认的范围、交接和验收依据。", "objections": []})


class DeepSeekProviderTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"DEEPSEEK_API_KEY": "credential-for-testing", "DEEPSEEK_FAST_MODEL": "", "DEEPSEEK_REASONING_MODEL": ""}, clear=False)
        env.start()
        self.addCleanup(env.stop)

    def test_default_factory_does_not_enable_live_mode_even_when_key_is_set(self):
        self.assertIsInstance(make_provider(), DemoProvider)
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            with self.assertRaises(ProviderError):
                make_provider("deepseek")

    def test_selected_model_request_and_usage_are_sanitized(self):
        provider = make_provider("deepseek")
        response = {
            "model": "DeepSeek-V4-Pro-0813",
            "choices": [{"message": {"content": '{"passed": true}', "reasoning_content": "not public"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "prompt_cache_hit_tokens": 20, "prompt_cache_miss_tokens": 80},
        }
        with patch("workflow.provider._open_request", return_value=io.BytesIO(json.dumps(response).encode())) as send:
            result = provider.generate("challenger", "检查方案", {"kind": "evaluation"}, "strong")
        request, timeout = send.call_args.args
        body = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.deepseek.com/chat/completions")
        self.assertGreater(timeout, 0)
        self.assertEqual(request.get_header("Authorization"), "Bearer credential-for-testing")
        self.assertNotIn("credential-for-testing", request.data.decode())
        self.assertEqual(body["model"], "deepseek-v4-pro")
        self.assertEqual(body["thinking"], {"type": "enabled"})
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertIn("json", body["messages"][0]["content"])
        self.assertEqual(result["model"], "DeepSeek-V4-Pro-0813")
        self.assertEqual(result["usage"]["total_tokens"], 120)
        self.assertAlmostEqual(result["usage"]["estimated_cost_usd"], (80 * 1.32 + 20 * 0.044 + 20 * 3.96) / 1_000_000)
        self.assertNotIn("not public", json.dumps(result))

    def test_unparseable_result_keeps_usage_and_http_error_never_echoes_secret(self):
        provider = DeepSeekProvider()
        response = {"choices": [{"message": {"content": "   "}}], "usage": {"prompt_tokens": 10, "completion_tokens": 4}}
        with patch("workflow.provider._open_request", return_value=io.BytesIO(json.dumps(response).encode())) as send:
            with self.assertRaises(ProviderError) as caught:
                provider.generate("recorder", "归纳", {"kind": "cluster"}, "balanced")
        self.assertEqual(caught.exception.usage["total_tokens"], 14)
        sent = json.loads(send.call_args.args[0].data)
        self.assertEqual(sent["model"], "deepseek-flash")
        self.assertEqual(sent["thinking"], {"type": "disabled"})
        failure = HTTPError("https://api.deepseek.com/chat/completions", 429, "sensitive authorization", {}, io.BytesIO(b"credential-for-testing"))
        with patch("workflow.provider._open_request", side_effect=failure):
            with self.assertRaises(ProviderError) as caught:
                provider.generate("recorder", "归纳", {"kind": "cluster"}, "economy")
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(caught.exception.status, 429)
        self.assertNotIn("credential-for-testing", str(caught.exception))

    def test_connection_error_and_size_guard(self):
        provider = DeepSeekProvider()
        with patch("workflow.provider._open_request", side_effect=URLError("server details")):
            with self.assertRaises(ProviderError) as caught:
                provider.generate("recorder", "记录", {"kind": "brief"}, "economy")
        self.assertTrue(caught.exception.retryable)
        self.assertNotIn("server details", str(caught.exception))
        with patch("workflow.provider._open_request", side_effect=AssertionError("Unexpected network")):
            with self.assertRaises(ProviderError):
                provider.generate("recorder", "记录", {"kind": "brief", "idea": "字" * 50_000}, "economy")


if __name__ == "__main__":
    unittest.main()
