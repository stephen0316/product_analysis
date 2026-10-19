"""Transport compatibility and actual model selection, without network access."""
from io import BytesIO
import json
import os
import unittest
from unittest.mock import patch

from post_evaluation import llm_client


class LLMClientTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def response(self, value):
        return BytesIO(json.dumps(value).encode())

    def completion(self, content='{"ok":true}', reason="stop"):
        return {"choices": [{"finish_reason": reason, "message": {"content": content}}]}

    def test_gateway_sends_configured_model_key_schema_and_limits(self):
        with patch.dict(os.environ, LLM_API_KEY="gateway-key", LLM_MODEL="gateway-model",
                        LLM_BASE_URL="https://gateway.example/v1/"), \
                patch.object(llm_client, "urlopen", return_value=self.response(self.completion())) as send:
            result = llm_client.chat_json("system", "business-data", {"type": "object"},
                                          temperature=.2, max_tokens=4096, timeout=60)
        request = send.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(request.full_url, "https://gateway.example/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer gateway-key")
        self.assertEqual(payload["model"], "gateway-model")
        self.assertEqual(payload["max_tokens"], 4096)
        self.assertEqual(payload["messages"][1]["content"], "business-data")
        self.assertIn('{"type": "object"}', payload["messages"][0]["content"])
        self.assertEqual(send.call_args.kwargs["timeout"], 60)
        self.assertIsNotNone(send.call_args.kwargs["context"])

    def test_google_key_keeps_original_endpoint_protocol_and_schema(self):
        raw = {"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"thought": True, "text": "not output"}, {"text": '{"ok":true}'}]}}]}
        with patch.dict(os.environ, GEMINI_API_KEY="google-key", GEMINI_MODEL="google-model",
                        LLM_BASE_URL="https://gateway.example/v1", LLM_MODEL="unrelated"), \
                patch.object(llm_client, "urlopen", return_value=self.response(raw)) as send:
            self.assertEqual(llm_client.provider(), "gemini")
            self.assertEqual(llm_client.chat_json("system", "data", {"type": "object"}), {"ok": True})
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, llm_client.GEMINI_API_ROOT + "/models/google-model:generateContent")
        self.assertEqual(request.get_header("X-goog-api-key"), "google-key")
        self.assertIsNone(request.get_header("Authorization"))
        self.assertEqual(json.loads(request.data)["generationConfig"]["responseJsonSchema"], {"type": "object"})

    def test_gateway_credentials_do_not_fall_back_to_google_model(self):
        with patch.dict(os.environ, LLM_API_KEY="gateway-key", GEMINI_API_KEY="google-key",
                        GEMINI_MODEL="google-model", GEMINI_REPORT_MODEL="google-report"):
            self.assertEqual(llm_client.provider(), "openai")
            self.assertEqual(llm_client.api_key(), "gateway-key")
            self.assertEqual(llm_client.model(), "qwen3.7-max")
            self.assertEqual(llm_client.report_model(), "qwen3.7-max")

    def test_report_override_is_sent_and_blank_override_uses_general_model(self):
        for settings in [dict(LLM_API_KEY="key", LLM_MODEL="general", LLM_REPORT_MODEL="report"),
                         dict(GEMINI_API_KEY="key", GEMINI_MODEL="general", GEMINI_REPORT_MODEL="report")]:
            with patch.dict(os.environ, settings):
                self.assertEqual(llm_client.report_model(), "report")
                report_key = "LLM_REPORT_MODEL" if "LLM_API_KEY" in settings else "GEMINI_REPORT_MODEL"
                with patch.dict(os.environ, {report_key: "   "}):
                    self.assertEqual(llm_client.report_model(), "general")
        with patch.dict(os.environ, LLM_API_KEY="key"), \
                patch.object(llm_client, "urlopen", return_value=self.response(self.completion())) as send:
            llm_client.chat_json("system", "data", model_name="report")
            self.assertEqual(json.loads(send.call_args.args[0].data)["model"], "report")

    def test_unconfigured_does_not_send(self):
        with patch.object(llm_client, "urlopen") as send:
            self.assertFalse(llm_client.configured())
            with self.assertRaisesRegex(ValueError, "尚未配置"):
                llm_client.chat_json("system", "data")
            send.assert_not_called()

    def test_gateway_rejects_empty_truncated_blocked_and_malformed_responses(self):
        for raw in [{}, {"choices": []}, {"choices": [None]},
                    self.completion(reason="length"), self.completion(reason="content_filter"),
                    self.completion(content="not-json"), self.completion(content=None)]:
            with self.subTest(raw=raw), patch.dict(os.environ, LLM_API_KEY="key"), \
                    patch.object(llm_client, "urlopen", return_value=self.response(raw)):
                with self.assertRaises(ValueError):
                    llm_client.chat_json("system", "data")

    def test_google_rejects_truncated_and_blocked_responses(self):
        for reason in ["MAX_TOKENS", "SAFETY"]:
            with patch.dict(os.environ, GEMINI_API_KEY="key"), \
                    patch.object(llm_client, "urlopen", return_value=self.response(
                        {"candidates": [{"finishReason": reason}]})):
                with self.assertRaisesRegex(ValueError, "截断" if reason == "MAX_TOKENS" else "中止"):
                    llm_client.chat_json("system", "data")

    def test_json_fences_are_accepted(self):
        self.assertEqual(llm_client.parse_json_object('```json\n{"ok":true}\n```'), {"ok": True})
