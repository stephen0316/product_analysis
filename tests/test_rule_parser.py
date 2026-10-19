import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from fastapi.testclient import TestClient

from post_evaluation.app import app
from test_semantic_rules import example_plan


SAMPLE_RULE = {
    "normalized_summary": "取最新年度市场份额，降序排名赋分。",
    "ranking_direction": "descending",
    "time_window": "latest_year",
    "required_inputs": [
        {"name": "最新年度市场份额", "concept": "市场份额", "period": "latest_year", "required": True},
    ],
    "tie_method": "competition",
    "steps": [
        {"order": 1, "operation": "select_data", "description": "取最新年度市场份额", "formula": "latest_year(market_share)"},
        {"order": 2, "operation": "rank", "description": "按市场份额降序排名", "formula": "rank_desc(value)"},
        {"order": 3, "operation": "assign_score", "description": "按名次赋分", "formula": "10 - 0.5 × (rank - 1)"},
    ],
    "parameters": {
        "first_score": 10,
        "rank_decrement": 0.5,
        "cap_percent": None,
        "growth_denominator_uses_absolute_value": None,
    },
    "assumptions": [],
    "warnings": [],
    "confidence": 0.98,
}


class RuleParserTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_reports_missing_configuration_without_exposing_credentials(self):
        with patch.dict(os.environ, {"LLM_API_KEY": "", "GEMINI_API_KEY": ""}):
            config = self.client.get("/api/config")
            self.assertEqual(config.status_code, 200)
            self.assertFalse(config.json()["rule_parser"]["configured"])
            response = self.client.post("/api/rules/parse", json={
                "dimension_label": "市场份额",
                "summary": "以市场占有率为依据，数值最大的产品排在最前，其余依次排列并赋分。",
            })
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("test-only-key", response.text)

    def test_returns_schema_constrained_gateway_result(self):
        api_response = {
            "choices": [{"finish_reason": "STOP", "message": {"content": json.dumps({"normalized_summary": "按市场占有率排名赋分", "execution_plan": example_plan("market_share"), "confidence": .98}, ensure_ascii=False)}}]
        }
        captured = {}

        def fake_urlopen(request, timeout, context):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["ssl_context"] = context
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return io.BytesIO(json.dumps(api_response, ensure_ascii=False).encode("utf-8"))

        with patch.dict(os.environ, {"LLM_API_KEY": "test-only-key", "LLM_MODEL": "qwen3.7-max"}):
            with patch("post_evaluation.llm_client.urlopen", side_effect=fake_urlopen):
                response = self.client.post("/api/rules/parse", json={
                    "dimension_label": "市场份额",
                    "summary": "以市场占有率为依据，数值最大的产品排在最前，其余依次排列并赋分。",
                })

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["rule"]["ranking_direction"], "descending")
        self.assertEqual(response.json()["rule"]["required_inputs"][0]["concept"], "market_share")
        self.assertEqual(response.json()["model"], "qwen3.7-max")
        self.assertNotIn("test-only-key", captured["url"])
        self.assertEqual(captured["timeout"], 30)
        self.assertEqual(captured["payload"]["model"], "qwen3.7-max")
        self.assertEqual(response.json()["provider"], "openai")
        self.assertIsNotNone(captured["ssl_context"])
        schema = json.loads(captured["payload"]["messages"][0]["content"].rsplit("\n", 1)[1])
        self.assertEqual(schema["type"], "object")
        self.assertIn("execution_plan", schema["required"])
        self.assertIn("不要把增幅", captured["payload"]["messages"][0]["content"])

    def test_parses_weight_optimization_goal_without_calculating_weights(self):
        api_response = {
            "choices": [{"finish_reason": "STOP", "message": {"content": json.dumps({
                "target_product": "千里眼",
                "direction": "down",
                "positions": 3,
                "normalized_goal": "让千里眼排名后移三位",
                "assumptions": [],
                "warnings": [],
                "confidence": 1,
            }, ensure_ascii=False)}}]
        }
        captured = {}

        def fake_urlopen(request, timeout, context):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return io.BytesIO(json.dumps(api_response, ensure_ascii=False).encode("utf-8"))

        with patch.dict(os.environ, {"LLM_API_KEY": "test-only-key", "LLM_MODEL": "qwen3.7-max"}):
            with patch("post_evaluation.llm_client.urlopen", side_effect=fake_urlopen):
                from post_evaluation.gemini_rules import parse_weight_goal
                result = parse_weight_goal("让千里眼排名后移三位", [
                    "OnePark", "芯片", "通信模组", "物联卡", "OneSec", "OneNET", "交通物流",
                    "商客标品", "商客场景", "5G行业产品", "千里眼", "和对讲", "云视讯",
                ])

        self.assertEqual(result["goal"]["target_product"], "千里眼")
        self.assertEqual(result["goal"]["positions"], 3)
        self.assertIn("只提取目标产品", captured["payload"]["messages"][1]["content"])
        self.assertIn("不计算权重", captured["payload"]["messages"][0]["content"])

    def test_schema_service_failure_is_not_reported_as_ambiguous_business_wording(self):
        from post_evaluation.gemini_rules import parse_rule_summary, GeminiRuleError
        detail = "The specified schema produces a constraint that has too many states for serving. test-only-key"
        error = HTTPError("https://generativelanguage.googleapis.com/", 400, "Bad Request", {}, io.BytesIO(json.dumps({"error": {"message": detail}}).encode()))
        with patch.dict(os.environ, LLM_API_KEY="test-only-key"), patch("post_evaluation.llm_client.urlopen", side_effect=error) as request:
            with self.assertRaises(GeminiRuleError) as caught:
                parse_rule_summary("经营表现", "按规模与成长综合评估。", source_hint="revenue")
        self.assertEqual(request.call_count, 1)
        self.assertIn("输出格式超出服务支持范围", str(caught.exception))
        self.assertIn("无需修改规则文字", str(caught.exception))
        self.assertNotIn("test-only-key", str(caught.exception))
        with patch("post_evaluation.app.parse_rule_summary", side_effect=caught.exception):
            response = self.client.post("/api/rules/parse", json={"dimension_label": "经营表现", "summary": "按规模与成长综合评估。"})
        self.assertEqual(response.status_code, 502)
        self.assertIn("无需修改规则文字", response.json()["detail"])

    def test_other_gemini_errors_keep_redacted_diagnostics(self):
        from post_evaluation.gemini_rules import parse_rule_summary, GeminiRuleError
        detail = "Permission denied test-only-key"
        error = HTTPError("https://generativelanguage.googleapis.com/", 403, "Forbidden", {}, io.BytesIO(json.dumps({"error": {"message": detail}}).encode()))
        with patch.dict(os.environ, LLM_API_KEY="test-only-key"), patch("post_evaluation.llm_client.urlopen", side_effect=error):
            with self.assertRaises(GeminiRuleError) as caught:
                parse_rule_summary("经营表现", "按规模与成长综合评估。", source_hint="revenue")
        self.assertIn("Permission denied", str(caught.exception))
        self.assertIn("[REDACTED]", str(caught.exception))
        self.assertNotIn("test-only-key", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
