from copy import deepcopy
import os
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from post_evaluation.app import app

from post_evaluation.calculation_types import compile_rule, local_rule, rule_readiness
from post_evaluation.model_defaults import default_model_definition
from post_evaluation.model_engine import validate_definition
from test_execution_boundary import calculate_sample
from test_rule_parser import SAMPLE_RULE
from post_evaluation.constants import PRODUCTS


class NaturalBusinessRulesTests(unittest.TestCase):
    def test_economic_synonyms_identify_and_produce_identical_scores(self):
        suffix = "；增幅按（当年值－上年值）÷ABS（上年值）计算，结果保留正负号。"
        variants = (
            "当年值与增幅分别降序排名赋分，取平均后再次排名赋分",
            "当年值与增幅分别降序排名并赋分，取平均后再次排名赋分",
            "当年值和增幅各自从高到低排序然后给分，取平均分后重新排序并给分",
            "当年收入与收入同比增长率分别从大到小排名并且赋分，取平均得分后再次降序排名再赋分",
        )
        baseline = calculate_sample(default_model_definition())
        client = TestClient(app)
        with patch("post_evaluation.app.parse_rule_summary", side_effect=AssertionError("同义本地规则不应联网")):
            for prefix in variants:
                text = prefix + suffix
                with self.subTest(text=text):
                    response = client.post("/api/rules/parse", json={"dimension_label": "收入／增幅", "summary": text})
                    self.assertEqual(response.status_code, 200, response.text)
                    definition = default_model_definition()
                    definition["dimensions"][1].update(rule_summary=text, options={"parsed_rule": response.json()["rule"], "rule_parser": {"source_summary": text}})
                    self.assertEqual(validate_definition(definition, require_100=True), [])
                    actual = calculate_sample(definition)
                    self.assertEqual(actual["scores"], baseline["scores"])
                    self.assertEqual(actual["model_summary"]["rows"], baseline["model_summary"]["rows"])

    def test_ai_growth_expressions_compare_structure_and_allow_implicit_selection(self):
        indicator = default_model_definition()["dimensions"][1]
        text = "用本年经营收入及其同比变动分别确定名次并给分，再对两个得分的均值排名计分，变动的分母为去年收入绝对值。"
        parsed = local_rule("revenue", indicator["rule_summary"])
        parsed["steps"] = parsed["steps"][1:]
        for index, step in enumerate(parsed["steps"], 1):
            step["order"] = index
        parsed["parameters"]["growth_denominator_uses_absolute_value"] = None
        indicator.update(rule_summary=text, options={"parsed_rule": parsed, "rule_parser": {"source_summary": text}})
        for formula in ("growth_rate = (current_year_value - previous_year_value) / ABS(previous_year_value)",
                        "（当年收入－上年收入）÷ABS（上年收入）", "(current + (-previous)) / abs(previous)"):
            with self.subTest(formula=formula):
                parsed["steps"][0]["formula"] = formula
                self.assertTrue(rule_readiness(indicator)["executable"], rule_readiness(indicator))
                self.assertTrue(compile_rule(indicator, "revenue")["growth_denominator_absolute"])
        average = next(step for step in parsed["steps"] if step["operation"] == "average")
        for formula in ("AVERAGE(当年值得分, 增幅得分)", "(current_score + growth_score) / 2", "0.5 * current_score + 0.5 * growth_score"):
            average["formula"] = formula
            self.assertTrue(rule_readiness(indicator)["executable"], rule_readiness(indicator))
        for formula in ("(current_score + growth_score) / 3", "0.8*current_score+0.2*growth_score", "AVERAGE(当年收入, 增幅)"):
            average["formula"] = formula
            self.assertFalse(rule_readiness(indicator)["executable"], formula)
        average["formula"] = "(当年值得分+增幅得分)/2"
        parsed["parameters"]["growth_denominator_uses_absolute_value"] = False
        self.assertFalse(rule_readiness(indicator)["executable"])
        parsed["parameters"]["growth_denominator_uses_absolute_value"] = None
        for formula in ("abs(current-previous)/abs(previous)", "(current-previous)/current", "__import__('os').system('echo unsafe')"):
            parsed["steps"][0]["formula"] = formula
            self.assertFalse(rule_readiness(indicator)["executable"], formula)
        with patch("post_evaluation.app.parse_rule_summary", return_value={"rule": parsed, "provider": "gemini", "model": "test", "parsed_at": "test"}):
            parsed["steps"][0]["formula"] = "(current_year_value-previous_year_value)/ABS(previous_year_value)"
            response = TestClient(app).post("/api/rules/parse", json={"dimension_label": "收入／增幅", "summary": text})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["calculation_type"], "revenue")

    def test_equivalent_economic_ascending_and_denominator_meaning_are_preserved(self):
        prefix = "当年值与增幅分别从低到高排序并给分，取平均后再次排名并赋分；"
        indicator = default_model_definition()["dimensions"][1]
        indicator["rule_summary"] = prefix + "增幅按(当年值-上年值)/(上年值)计算，结果保留正负号。"
        compiled = compile_rule(indicator, "revenue")
        self.assertEqual(compiled["ranking"]["direction"], "ascending")
        self.assertEqual(compiled["final_ranking"]["direction"], "descending")
        self.assertFalse(compiled["growth_denominator_absolute"])
        for text in (prefix + "增幅按ABS(当年值-上年值)/ABS(上年值)计算，结果保留正负号。",
                     prefix + "增幅按(当年值-上年值)/ABS(上年值)计算，结果统一取正数。",
                     prefix.replace("再次排名", "再次升序排名") + "增幅按(当年值-上年值)/ABS(上年值)计算，结果保留正负号。",
                     prefix.replace("取平均后", "取最大值后") + "增幅按(当年值-上年值)/ABS(上年值)计算，结果保留正负号。"):
            indicator["rule_summary"] = text
            self.assertFalse(rule_readiness(indicator)["executable"], text)

    def test_equivalent_business_ranking_language_works_without_ai(self):
        for text in (
            "按市场份额大小排名", "市场份额越大，排名越靠前。", "根据最新年度市场份额从高到低排序。",
            "取评估年度市场份额，按从大到小的顺序排名赋分。", "市场份额越高，得分越高。",
            "按市场份额降序排名，第一名得10分，每下降一名扣0.5分。",
        ):
            with self.subTest(text=text):
                indicator = default_model_definition()["dimensions"][0]
                indicator["rule_summary"] = text
                self.assertTrue(rule_readiness(indicator)["executable"], rule_readiness(indicator))
                self.assertEqual(compile_rule(indicator, "market_share")["ranking"]["direction"], "descending")

    def test_natural_ascending_language_changes_real_scores(self):
        for text in ("市场份额从小到大排名", "按市场份额由低到高排序。", "市场份额越低，得分越高。"):
            with self.subTest(text=text):
                definition = default_model_definition()
                definition["dimensions"][0]["rule_summary"] = text
                result = calculate_sample(definition)
                self.assertEqual(result["scores"][PRODUCTS[-1]]["market_share"], 10)
                self.assertEqual(result["scores"][PRODUCTS[0]]["market_share"], 4)

    def test_natural_score_parameters_are_used(self):
        definition = default_model_definition()
        definition["dimensions"][0]["rule_summary"] = "按市场份额从大到小排名，第一名得20分，每下降一名扣1分。"
        result = calculate_sample(definition)
        self.assertEqual(result["scores"][PRODUCTS[0]]["market_share"], 20)
        self.assertEqual(result["scores"][PRODUCTS[-1]]["market_share"], 8)

    def test_unknown_conditions_and_contradictory_directions_do_not_fall_back(self):
        for text in ("市场份额大于20%额外加2分", "按市场份额从大到小排名，同时从小到大排名",
                     "所有产品直接得满分", "市场份额归一化后按分段给分", "根据投诉次数从大到小排名"):
            with self.subTest(text=text):
                indicator = default_model_definition()["dimensions"][0]
                indicator["rule_summary"] = text
                self.assertFalse(rule_readiness(indicator)["executable"])

    def test_legacy_parser_data_selection_notation_does_not_block_ranking(self):
        definition = default_model_definition()
        indicator = definition["dimensions"][0]
        parsed = deepcopy(SAMPLE_RULE)
        parsed["steps"][0]["formula"] = "选择最新年度表中的市场份额原值"
        parsed["steps"][1]["formula"] = "按原值从大到小排名"
        indicator["options"] = {"parsed_rule": parsed, "rule_parser": {"source_summary": indicator["rule_summary"]}}
        self.assertEqual(validate_definition(definition, require_100=True), [])
        for formula in ("10 - (名次 - 1) * 0.5", "first_score - decrement * (rank - 1)", "10.5 - 0.5 * rank"):
            parsed["steps"][2]["formula"] = formula
            self.assertEqual(validate_definition(definition, require_100=True), [], formula)
        for formula in ("10 - rank**2", "__import__('os').system('echo unsafe')", "value*2"):
            parsed["steps"][2]["formula"] = formula
            self.assertTrue(validate_definition(definition, require_100=True))

    def test_ai_interpretation_is_checked_semantically_not_against_fixed_text(self):
        indicator = default_model_definition()["dimensions"][0]
        text = "以公司产品在最新年度的市场占有率为依据，数值最大的产品排在最前，其余产品依次排列并赋分。"
        self.assertIsNone(local_rule("market_share", text))
        parsed = deepcopy(SAMPLE_RULE)
        parsed["required_inputs"][0]["concept"] = "市场占有率"
        parsed["normalized_summary"] = "按最新市场占有率从高到低排名。"
        parsed["steps"][0]["formula"] = "latest_market_share"
        indicator.update(rule_summary=text, options={"parsed_rule": parsed, "rule_parser": {"source_summary": text}})
        self.assertTrue(rule_readiness(indicator)["executable"], rule_readiness(indicator))
        parsed["required_inputs"][0]["concept"] = "客户投诉次数"
        self.assertFalse(rule_readiness(indicator)["executable"])

    def test_other_single_value_business_language_is_recognised(self):
        indicator = next(i for i in default_model_definition()["dimensions"] if i["key"] == "quality")
        indicator["rule_summary"] = "根据客户满意度从高到低排名赋分。"
        self.assertTrue(rule_readiness(indicator)["executable"])

    def test_plain_language_parse_save_and_publish_offline(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, PRODUCT_EVAL_DATA_DIR=directory, GEMINI_API_KEY=""), patch("post_evaluation.app.parse_rule_summary", side_effect=AssertionError("不应联网")):
            client = TestClient(app)
            original = client.get("/api/models").json()[0]
            for index, text in enumerate(("市场份额越大，排名越靠前。", "按市场份额从小到大排名。", "按市场份额大小排名")):
                with self.subTest(text=text):
                    draft = client.post("/api/models", json={"name": "自然语言验收", "version": f"NL-{index}", "clone_from": original["id"]}).json()
                    response = client.post("/api/rules/parse", json={"calculation_type": "market_share", "dimension_label": "市场份额", "summary": text})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["provider"], "local")
                    definition = draft["definition"]
                    definition["dimensions"][0].update(rule_summary=text, options={"parsed_rule": response.json()["rule"], "rule_parser": {"source_summary": text}})
                    body = {"name": draft["name"], "version": draft["version"], "definition": definition}
                    self.assertEqual(client.put(f"/api/models/{draft['id']}", json=body).status_code, 200)
                    released = client.post(f"/api/models/{draft['id']}/publish", json=body)
                    self.assertEqual(released.status_code, 200, released.text)

    def test_legacy_ai_notes_do_not_override_clear_business_meaning(self):
        definition = default_model_definition()
        indicator = definition["dimensions"][0]
        parsed = deepcopy(SAMPLE_RULE)
        parsed.update(steps=parsed["steps"][:2], ranking_direction="unclear", warnings=["赋分方式未逐项展开"])
        indicator["options"] = {"parsed_rule": parsed, "rule_parser": {"provider": "gemini", "source_summary": indicator["rule_summary"]}}
        before = deepcopy(indicator)
        self.assertTrue(rule_readiness(indicator)["executable"])
        self.assertEqual(indicator, before)
        parsed["parameters"]["first_score"] = 0
        self.assertFalse(rule_readiness(indicator)["executable"])

    def test_legacy_ai_denominator_follows_explicit_formula_without_mutating_snapshot(self):
        for type_id in ("revenue", "gross_profit", "gross_margin", "operating_profit", "operating_margin"):
            for absolute in (True, False):
                with self.subTest(type_id=type_id, absolute=absolute):
                    indicator = deepcopy(default_model_definition()["dimensions"][1])
                    indicator["calculation_type"] = type_id
                    if not absolute:
                        indicator["rule_summary"] = indicator["rule_summary"].replace("ABS（上年值）", "（上年值）")
                    parsed = local_rule(type_id, indicator["rule_summary"])
                    parsed["parameters"]["growth_denominator_uses_absolute_value"] = not absolute
                    indicator["options"] = {"parsed_rule": parsed, "rule_parser": {"provider": "gemini", "source_summary": indicator["rule_summary"]}}
                    before = deepcopy(indicator)
                    self.assertTrue(rule_readiness(indicator)["executable"])
                    self.assertEqual(compile_rule(indicator, type_id)["growth_denominator_absolute"], absolute)
                    self.assertEqual(indicator, before)
                    indicator["options"]["rule_parser"]["execution_contract"] = "supported-types-v2"
                    self.assertFalse(rule_readiness(indicator)["executable"])

    def test_new_indicator_automatically_binds_and_calculates_without_a_selector(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, PRODUCT_EVAL_DATA_DIR=directory, GEMINI_API_KEY=""), patch("post_evaluation.app.parse_rule_summary", side_effect=AssertionError("不应联网")):
            client = TestClient(app)
            original = client.get("/api/models").json()[0]
            draft = client.post("/api/models", json={"name": "自动绑定验收", "version": "AUTO", "clone_from": original["id"]}).json()
            text = "按市场份额从小到大排名。"
            response = client.post("/api/rules/parse", json={"dimension_label": "市场竞争表现", "summary": text})
            self.assertEqual(response.status_code, 200, response.text)
            value = response.json()
            self.assertEqual(value["calculation_type"], "market_share")
            definition = draft["definition"]
            definition["dimensions"][0]["weight"] = 0
            indicator = deepcopy(definition["dimensions"][0])
            indicator.update(key="custom_share", label="市场竞争表现", metric_keys=["custom_share"], weight=10,
                             calculation_type=value["calculation_type"], rule_summary=text,
                             options={"parsed_rule": value["rule"], "rule_parser": {"source_summary": text}})
            definition["dimensions"].append(indicator)
            body = {"name": draft["name"], "version": draft["version"], "definition": definition}
            self.assertEqual(client.put(f"/api/models/{draft['id']}", json=body).status_code, 200)
            released = client.post(f"/api/models/{draft['id']}/publish", json=body)
            self.assertEqual(released.status_code, 200, released.text)
            self.assertEqual(calculate_sample(definition)["scores"][PRODUCTS[-1]]["custom_share"], 10)

    def test_explicit_data_in_summary_overrides_old_source_hint(self):
        with patch.dict(os.environ, GEMINI_API_KEY=""), patch("post_evaluation.app.parse_rule_summary", side_effect=AssertionError("不应联网")):
            response = TestClient(app).post("/api/rules/parse", json={"dimension_label": "服务表现", "calculation_type": "market_share", "summary": "按客户满意度从高到低排名赋分。"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["calculation_type"], "quality")

    def test_implicit_economic_data_uses_indicator_name_and_does_not_guess(self):
        text = default_model_definition()["dimensions"][1]["rule_summary"]
        client = TestClient(app)
        with patch("post_evaluation.app.parse_rule_summary", side_effect=AssertionError("不应联网")):
            response = client.post("/api/rules/parse", json={"dimension_label": "收入／增幅", "summary": text})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["calculation_type"], "revenue")
        from test_semantic_rules import example_plan
        unresolved = {"rule": {"execution_plan": example_plan(questions=["请明确原始数据名称：收入还是毛利？"])}, "provider": "gemini", "model": "test", "parsed_at": "test"}
        with patch("post_evaluation.app.parse_rule_summary", return_value=unresolved):
            response = client.post("/api/rules/parse", json={"dimension_label": "新指标", "summary": text})
            self.assertEqual(response.status_code, 400, response.text)
            self.assertIn("原始数据名称", response.json()["detail"])

    def test_ai_business_input_can_bind_renamed_indicator_without_hint(self):
        text = "以公司产品在最新年度的市场占有率为依据，数值最大的产品排在最前，其余产品依次排列并赋分。"
        with patch("post_evaluation.app.parse_rule_summary", return_value={"rule": deepcopy(SAMPLE_RULE), "provider": "gemini", "model": "test", "parsed_at": "test"}):
            response = TestClient(app).post("/api/rules/parse", json={"dimension_label": "市场竞争表现", "summary": text})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["calculation_type"], "market_share")

    def test_automatic_binding_still_blocks_unsupported_and_invalid_rules(self):
        parsed = deepcopy(SAMPLE_RULE)
        parsed["required_inputs"][0]["concept"] = "投诉次数"
        with patch("post_evaluation.app.parse_rule_summary", return_value={"rule": parsed, "provider": "gemini", "model": "test", "parsed_at": "test"}):
            response = TestClient(app).post("/api/rules/parse", json={"dimension_label": "市场份额", "summary": "按投诉次数排名"})
        self.assertEqual(response.status_code, 400, response.text)
        response = TestClient(app).post("/api/rules/parse", json={"dimension_label": "市场份额", "summary": "按市场份额从大到小排名，第一名得0分，每下降一名扣0.5分。"})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("首名分", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
