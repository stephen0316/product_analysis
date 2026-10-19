from copy import deepcopy
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from post_evaluation.app import app
from post_evaluation.calculation_types import compile_rule, local_rule, rule_readiness
from post_evaluation.constants import PRODUCTS
from post_evaluation.model_defaults import default_model_definition
from post_evaluation.semantic_rules import CONTRACT, input_contract, source_strategy, normalize_interpretation, read_plan, specified_year
from post_evaluation.scoring import calculate
from test_execution_boundary import calculate_sample, sample_records


def example_plan(source="revenue", **changes):
    strategy = source_strategy(source)
    composite = strategy in {"economic", "three_eff", "self_research"}
    plan = {"version": CONTRACT, "source_metric": source, "strategy": strategy,
            "inputs": input_contract(source), "data_mode": "annual_demo" if source == "ability" else "source",
            "ranking_direction": "descending", "final_ranking_direction": "descending" if composite else "not_applicable",
            "tie_method": "competition", "time_window": "latest_two_years" if strategy == "economic" else "latest_year",
            "aggregation": "equal_scores" if composite else "none", "growth_denominator": "absolute_previous" if strategy == "economic" else "not_applicable",
            "growth_preserve_sign": True if strategy == "economic" else None, "cap_percent": None,
            "first_score": None, "rank_decrement": None, "unsupported_features": [], "questions": []}
    return {**plan, **changes}


def indicator_with_plan(source="revenue", summary="换一种完全不同的日常表达，不提供程序公式。", **changes):
    indicator = next(item for item in default_model_definition()["dimensions"] if item["calculation_type"] == source)
    indicator.update(rule_summary=summary, options={"parsed_rule": {"execution_plan": example_plan(source, **changes)},
                                                "rule_parser": {"source_summary": summary, "execution_contract": CONTRACT}})
    return indicator


class SemanticRulesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, PRODUCT_EVAL_DATA_DIR=self.directory.name)
        self.environment.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.environment.stop()
        self.directory.cleanup()

    def test_free_wording_uses_the_same_typed_plan_and_real_scores(self):
        variants = (
            "别只看今年挣了多少，也看比去年长了多少：两方面先各排一次、各拿分，再把两张成绩单平均，最后按这个均分排出名次给分。",
            "规模和成长各占一票，先比营收规模、再比同比变化，把两项的名次换成分数后求均分，以均分重新决定总名次。",
            "今年营收与同比增速分别论英雄，综合二者的赋分均值再来一轮排序评分；基数用去年的绝对数，增减方向保留。",
        )
        original = self.client.get("/api/models").json()[0]
        baseline = calculate_sample(default_model_definition())
        fake = {"rule": {"execution_plan": example_plan(), "normalized_summary": "含义相同，无固定句式或公式"}, "provider": "gemini", "model": "test", "parsed_at": "test"}
        with patch("post_evaluation.app.parse_rule_summary", return_value=fake) as parser:
            for index, text in enumerate(variants):
                self.assertIsNone(local_rule("revenue", text))
                response = self.client.post("/api/rules/parse", json={"dimension_label": "经营增长表现", "summary": text, "calculation_type": "revenue"})
                self.assertEqual(response.status_code, 200, response.text)
                value = response.json()
                self.assertEqual(value["execution_contract"], CONTRACT)
                self.assertIn("等权平均", value["rule"]["normalized_summary"])
                self.assertEqual(value["rule"]["steps"], [])
                definition = default_model_definition()
                definition["dimensions"][1].update(label="经营增长表现", rule_summary=text, options={"parsed_rule": value["rule"], "rule_parser": {"source_summary": text}})
                actual = calculate_sample(definition)
                self.assertEqual(actual["scores"], baseline["scores"])
                self.assertEqual([row["total"] for row in actual["model_summary"]["rows"]], [row["total"] for row in baseline["model_summary"]["rows"]])
                draft = self.client.post("/api/models", json={"name": "语义方案验收", "version": f"SEM-{index}", "clone_from": original["id"]}).json()
                body = {"name": draft["name"], "version": draft["version"], "definition": definition}
                self.assertEqual(self.client.put(f"/api/models/{draft['id']}", json=body).status_code, 200)
                self.assertEqual(self.client.post(f"/api/models/{draft['id']}/publish", json=body).status_code, 200)
                reread = self.client.get(f"/api/models/{draft['id']}").json()
                self.assertEqual(reread["definition"]["dimensions"][1]["options"]["parsed_rule"]["execution_plan"], example_plan())
        self.assertEqual(parser.call_count, len(variants))

    def test_backend_ignores_explanatory_prose_and_checks_the_plan(self):
        indicator = indicator_with_plan(summary="不额外加分；只按大家理解的方式综合评价。")
        indicator["options"]["parsed_rule"].update(normalized_summary="不是程序公式：α、β、∑", steps=[{"formula": "任意解释文字；不参与执行"}])
        self.assertTrue(rule_readiness(indicator)["executable"])
        compiled = compile_rule(indicator, "revenue")
        self.assertIn("当年值与同比增幅", compiled["rule"]["normalized_summary"])
        self.assertEqual(compiled["rule"]["steps"], [])

    def test_typed_parameters_reach_deterministic_engine_and_inherit_model_settings(self):
        definition = default_model_definition()
        definition["dimensions"][0] = indicator_with_plan("market_share", ranking_direction="ascending", first_score=20, rank_decrement=1)
        definition["dimensions"][1] = indicator_with_plan(growth_denominator="previous")
        definition["dimensions"][7] = indicator_with_plan("self_research", cap_percent=50)
        records = sample_records()
        for record in records:
            if record["field"] == "revenue:2024":
                record["value"] = -10
        result = calculate_sample(definition, records)
        self.assertEqual(result["scores"][PRODUCTS[-1]]["market_share"], 20)
        self.assertEqual(result["scores"][PRODUCTS[0]]["market_share"], 8)
        trace = next(t for t in result["traces"] if t["metric"] == "revenue" and t["product"] == PRODUCTS[0])
        self.assertEqual(trace["steps"][2]["value"], -5)
        research = compile_rule(definition["dimensions"][7], "self_research")
        self.assertEqual(research["cap"], .5)
        self.assertEqual(compile_rule(indicator_with_plan(), "revenue", {"first_score": 30, "decrement": 2})["ranking"]["first_score"], 30)

    def test_all_existing_sources_keep_their_default_scores_and_trace_sources(self):
        definition = default_model_definition()
        baseline = calculate_sample(definition)
        definition["dimensions"] = [indicator_with_plan(item["calculation_type"]) for item in definition["dimensions"]]
        actual = calculate_sample(definition)
        self.assertEqual(actual["scores"], baseline["scores"])
        self.assertEqual(actual["result_status"], baseline["result_status"])
        for trace in actual["traces"]:
            if trace["metric"] != "ability":
                self.assertTrue(any(step.get("source") for step in trace["steps"]))

    def test_plan_rejects_unknown_missing_ambiguous_or_unsupported_meanings(self):
        changes = (
            {"questions": ["你所说的增长是同比还是环比？"]},
            {"unsupported_features": ["投诉少于5次额外加2分"]},
            {"source_metric": "new_metric"}, {"strategy": "single"}, {"inputs": []},
            {"inputs": [{"field": "revenue", "period": "current_year"}, {"field": "invented", "period": "previous_year"}]},
            {"aggregation": "weighted_scores"}, {"aggregation": "raw_values"},
            {"growth_preserve_sign": False}, {"growth_denominator": "other"},
            {"final_ranking_direction": "ascending"}, {"ranking_direction": "unclear"},
            {"tie_method": "dense"}, {"time_window": "custom"}, {"data_mode": "annual_demo"},
            {"cap_percent": 50}, {"first_score": 0}, {"rank_decrement": -1},
            {"first_score": True}, {"first_score": float("inf")}, {"version": "future-version"},
            {"arbitrary_formula": "value*2"},
        )
        for change in changes:
            with self.subTest(change=change):
                indicator = indicator_with_plan(**change)
                self.assertFalse(rule_readiness(indicator)["executable"], change)
        indicator = indicator_with_plan()
        indicator["rule_summary"] += "已修改"
        self.assertFalse(rule_readiness(indicator)["executable"])

    def test_successful_interpretations_are_reused_but_context_changes_are_not(self):
        fake = {"rule": {"execution_plan": example_plan()}, "provider": "gemini", "model": "test", "parsed_at": "test"}
        body = {"dimension_label": "经营表现", "summary": "规模和成长的成绩先平均，然后按大家的综合成绩再排一轮。", "calculation_type": "revenue"}
        with patch("post_evaluation.app.parse_rule_summary", return_value=fake) as parser:
            first = self.client.post("/api/rules/parse", json=body)
            self.assertEqual(first.status_code, 200, first.text)
            second = self.client.post("/api/rules/parse", json=body)
            self.assertTrue(second.json()["cache_hit"])
            self.assertEqual(parser.call_count, 1)
            self.assertEqual(self.client.post("/api/rules/parse", json={**body, "ranking": {"first_score": 30, "decrement": 2}}).status_code, 200)
            self.assertEqual(parser.call_count, 2)
            self.client.post("/api/rules/parse", json={**body, "summary": body["summary"]+"取去年原值作分母。"})
            self.assertEqual(parser.call_count, 3)

    def test_failed_understanding_returns_specific_questions_and_is_not_cached(self):
        fake = {"rule": {"execution_plan": example_plan(questions=["请确认增长使用同比还是环比"] )}, "provider": "gemini", "model": "test", "parsed_at": "test"}
        body = {"dimension_label": "经营表现", "summary": "比较增长情况，给我综合排一下。"}
        with patch("post_evaluation.app.parse_rule_summary", return_value=fake) as parser:
            for _ in range(2):
                response = self.client.post("/api/rules/parse", json=body)
                self.assertEqual(response.status_code, 400)
                self.assertIn("同比还是环比", response.json()["detail"])
            self.assertEqual(parser.call_count, 2)

    def test_real_parser_protocol_contains_typed_plan_and_no_formula_schema(self):
        transport_plan = example_plan()
        transport_plan.pop("version")
        transport_plan.pop("growth_preserve_sign")
        transport_plan["growth_result"] = "signed_change"
        raw = {"normalized_summary": "今年收入与同比增幅的得分平均后再排名", "execution_plan": transport_plan, "confidence": .99}
        captured = {}
        def fake_urlopen(request, **kwargs):
            captured.update(json.loads(request.data.decode()))
            return io.BytesIO(json.dumps({"choices": [{"finish_reason": "STOP", "message": {"content": json.dumps(raw, ensure_ascii=False)}}]}).encode())
        from post_evaluation.gemini_rules import parse_rule_summary
        with patch.dict(os.environ, LLM_API_KEY="test-key"), patch("post_evaluation.llm_client.urlopen", side_effect=fake_urlopen):
            value = parse_rule_summary("经营增长表现", "综合比较规模与同比成长，把分数平均后再排。", source_hint="revenue")
        self.assertEqual(value["rule"]["execution_plan"], example_plan())
        schema = json.loads(captured["messages"][0]["content"].rsplit("\n", 1)[1])
        self.assertIn("execution_plan", schema["required"])
        self.assertNotIn("formula", json.dumps(schema))
        self.assertNotIn("$ref", json.dumps(schema))
        self.assertNotIn("version", schema["properties"]["execution_plan"]["properties"])
        self.assertIn("growth_result", schema["properties"]["execution_plan"]["properties"])
        self.assertNotIn("growth_preserve_sign", schema["properties"]["execution_plan"]["properties"])
        self.assertNotIn("time_window", schema["properties"]["execution_plan"]["properties"])
        input_schema = schema["properties"]["execution_plan"]["properties"]["inputs"]["items"]
        self.assertIn("year", input_schema["required"])
        self.assertEqual(input_schema["properties"]["year"]["type"], ["integer", "null"])
        self.assertIn("research_cost_resource_pool", captured["messages"][1]["content"])

    def test_protocol_version_is_owned_by_server_and_stored_versions_are_checked(self):
        plan = example_plan()
        for version in (None, "1.0", "random-version"):
            raw = deepcopy(plan)
            raw.pop("version")
            if version is not None:
                raw["version"] = version
            normalized = normalize_interpretation({"normalized_summary": "经营增长表现", "execution_plan": raw, "confidence": .95})
            self.assertEqual(normalized["execution_plan"]["version"], CONTRACT)
            self.assertEqual(raw.get("version"), version)
        with self.assertRaises(ValueError):
            read_plan({**plan, "version": "future-version"})

    def test_generation_schema_is_lightweight_without_weakening_server_validation(self):
        from post_evaluation.semantic_rules import response_schema
        schema = response_schema()
        serialized = json.dumps(schema)
        for keyword in ("maxItems", "minItems", "maxLength", "minLength", "minimum", "maximum", "anyOf", "default"):
            self.assertNotIn(f'"{keyword}"', serialized)
        properties = schema["properties"]["execution_plan"]["properties"]
        self.assertEqual(properties["source_metric"]["type"], ["string", "null"])
        self.assertEqual(properties["first_score"]["type"], ["number", "null"])
        self.assertIn("ascending", properties["ranking_direction"]["enum"])
        for changes in ({"inputs": input_contract("revenue")*26}, {"questions": ["待确认"]*21}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                normalize_interpretation({"normalized_summary": "测试", "execution_plan": example_plan(**changes), "confidence": .95})
        for summary, confidence in (("", .95), ("长"*3001, .95), ("测试", 1.1), ("测试", -.1)):
            with self.subTest(confidence=confidence, length=len(summary)), self.assertRaises(ValueError):
                normalize_interpretation({"normalized_summary": summary, "execution_plan": example_plan(), "confidence": confidence})

    def test_growth_semantic_enum_is_normalized_without_losing_sign(self):
        for mode, expected in (("signed_change", True), ("absolute_change", False), ("not_applicable", None)):
            raw = example_plan()
            raw.pop("version")
            raw.pop("growth_preserve_sign")
            raw["growth_result"] = mode
            result = normalize_interpretation({"normalized_summary": "增幅口径", "execution_plan": raw, "confidence": .95})
            self.assertIs(result["execution_plan"]["growth_preserve_sign"], expected)
        raw["growth_result"] = "unknown"
        with self.assertRaises(ValueError):
            normalize_interpretation({"normalized_summary": "增幅口径", "execution_plan": raw, "confidence": .95})

    def test_temporal_span_is_derived_from_raw_dependencies_not_an_independent_guess(self):
        for old_window in (None, "custom", "latest_year", "unclear"):
            raw = example_plan(time_window=old_window)
            if old_window is None:
                raw.pop("time_window")
            original = deepcopy(raw)
            normalized = normalize_interpretation({"normalized_summary": "取25年收入及同比", "execution_plan": raw, "confidence": .99})
            self.assertEqual(raw, original)
            self.assertEqual(normalized["execution_plan"]["time_window"], "latest_two_years")
            self.assertTrue(rule_readiness(indicator_with_plan(**normalized["execution_plan"]))["executable"])
        for source in ("market_share", "three_eff", "self_research", "ability", "quality"):
            raw = example_plan(source)
            raw.pop("time_window")
            normalized = normalize_interpretation({"normalized_summary": "单年度数据", "execution_plan": raw, "confidence": .99})
            self.assertEqual(normalized["execution_plan"]["time_window"], "latest_year")
            self.assertTrue(rule_readiness(indicator_with_plan(source, **normalized["execution_plan"]))["executable"])

    def test_explicit_years_normalize_and_keep_the_year_constraint(self):
        for current, previous in ((25, None), (2025, None), (25, 24), (2025, 2024), (None, 2024)):
            inputs = [{**item, "year": previous if item["period"] == "previous_year" else current} for item in input_contract("revenue")]
            normalized = normalize_interpretation({"normalized_summary": "取25年收入及同比", "execution_plan": example_plan(inputs=inputs), "confidence": .99})
            plan = read_plan(normalized["execution_plan"])
            self.assertEqual(specified_year(plan), 2025)
            compiled = compile_rule(indicator_with_plan(**normalized["execution_plan"]), "revenue")
            self.assertIn("明确限定2025年数据，同比基数为2024年", compiled["rule"]["normalized_summary"])
        plan = read_plan(example_plan())
        self.assertIsNone(specified_year(plan))
        for year in (2020, 2100):
            inputs = [{**item, "year": year-1 if item["period"] == "previous_year" else year} for item in input_contract("revenue")]
            self.assertTrue(rule_readiness(indicator_with_plan(inputs=inputs))["executable"])

    def test_nonadjacent_and_invalid_years_cannot_be_executed(self):
        inputs = [{**item, "year": 2023 if item["period"] == "previous_year" else 2025} for item in input_contract("revenue")]
        readiness = rule_readiness(indicator_with_plan(inputs=inputs))
        self.assertFalse(readiness["executable"])
        self.assertIn("不能跳年", "；".join(readiness["errors"]))
        self.assertFalse(rule_readiness(indicator_with_plan(inputs=[{**item, "year": 2019} for item in input_contract("revenue")]))["executable"])
        for invalid in (True, "2025", 2018, 2101, 0):
            raw = example_plan(inputs=[{**item, "year": invalid} for item in input_contract("revenue")])
            with self.subTest(year=invalid), self.assertRaises(ValueError):
                normalize_interpretation({"normalized_summary": "年度规则", "execution_plan": raw, "confidence": .99})

    def test_year_wordings_parse_offline_and_calculate_without_silently_switching_year(self):
        variants = (("取25 年收入", 25), ("取2025年收入", 2025), ("取当年收入", None))
        baseline = calculate_sample(default_model_definition())
        for prefix, year in variants:
            summary = prefix + "与增幅分别排名赋分，取平均后再次排名赋分；增幅按（当年值－上年值）÷ABS（上年值）计算，结果保留正负号。"
            self.assertIsNone(local_rule("revenue", summary))
            raw = example_plan(inputs=[{**item, "year": year if item["period"] == "current_year" else None} for item in input_contract("revenue")])
            raw.pop("time_window")
            normalized = normalize_interpretation({"normalized_summary": summary, "execution_plan": raw, "confidence": .99})
            fake = {"rule": normalized, "provider": "gemini", "model": "offline-fixture", "parsed_at": "test"}
            with patch("post_evaluation.app.parse_rule_summary", return_value=fake):
                response = self.client.post("/api/rules/parse", json={"dimension_label": "收入／增幅", "summary": summary, "calculation_type": "revenue"})
            self.assertEqual(response.status_code, 200, response.text)
            definition = default_model_definition()
            definition["dimensions"][1].update(rule_summary=summary, options={"parsed_rule": response.json()["rule"], "rule_parser": {"source_summary": summary}})
            self.assertEqual(calculate_sample(definition)["scores"], baseline["scores"])
            files = [{"id": "sample", "path": "unused", "role": "generic", "mapping": {}, "targets": [item["key"] for item in definition["dimensions"]]}]
            with patch("post_evaluation.scoring.extract_records", return_value=(sample_records(2026), [])):
                if year is None:
                    self.assertEqual(calculate(2026, files, {}, definition)["scores"][PRODUCTS[0]]["revenue"], 10)
                else:
                    with self.assertRaisesRegex(ValueError, "明确限定2025年.*年度为2026年"):
                        calculate(2026, files, {}, definition)


if __name__ == "__main__":
    unittest.main()
