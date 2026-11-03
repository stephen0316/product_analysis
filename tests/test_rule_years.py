from copy import deepcopy
from datetime import datetime
from io import BytesIO
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from auth_test_support import TestClient
from openpyxl import Workbook, load_workbook

from post_evaluation import store
from post_evaluation.app import app
from post_evaluation.calculation_types import local_rule, rule_readiness
from post_evaluation.constants import PRODUCTS, input_specs_for_dimension
from post_evaluation.exports import make_workbook
from post_evaluation.model_defaults import default_model_definition
from post_evaluation.model_mapping import match_model_workbook
from post_evaluation.rule_years import concrete_defaults, dimension_year
from post_evaluation.scoring import calculate
from test_semantic_rules import example_plan


def definition(year=2024, source="revenue"):
    model = concrete_defaults(default_model_definition(), year)
    item = next(i for i in model["dimensions"] if i["calculation_type"] == source)
    item["weight"] = 100
    model["dimensions"] = [item]
    return model


def workbook(headers, title="原始数据"):
    book = Workbook()
    ws = book.active
    ws.title = title
    ws.append(["产品线", *headers])
    for index, product in enumerate(PRODUCTS):
        ws.append([product, *[50 if header.startswith("2023") else 100-index for header in headers]])
    data = BytesIO()
    book.save(data)
    book.close()
    return data.getvalue()


class RuleYearTests(unittest.TestCase):
    def test_all_presets_have_concrete_year_and_are_locally_executable(self):
        for item in concrete_defaults(default_model_definition(), 2025)["dimensions"]:
            with self.subTest(source=item["calculation_type"]):
                self.assertIn("2025年", item["rule_summary"])
                self.assertTrue(rule_readiness(item)["executable"], rule_readiness(item))
                self.assertEqual(rule_readiness(item)["data_year"], 2025)
                self.assertEqual(dimension_year(item, 2027), 2025)

    def test_custom_rule_is_never_rewritten_as_default(self):
        model = definition()
        old = deepcopy(model)
        self.assertEqual(concrete_defaults(model, 2026), old)

    def test_two_digit_year_and_default_wording_generate_correct_inputs(self):
        item = definition()["dimensions"][0]
        item["rule_summary"] = item["rule_summary"].replace("2024年", "24 年").replace("2023年", "23 年")
        self.assertIsNotNone(local_rule("revenue", item["rule_summary"]))
        self.assertEqual([s["key"] for s in input_specs_for_dimension(item, 2025)], ["revenue:2023", "revenue:2024"])
        self.assertEqual(rule_readiness(item)["inputs"], ["2023年收入", "2024年收入"])

    def test_typed_plan_year_overrides_batch_year_and_must_match_summary(self):
        item = definition()["dimensions"][0]
        plan = example_plan("revenue")
        plan["inputs"] = [{**i, "year": 2023 if i["period"] == "previous_year" else 2024} for i in plan["inputs"]]
        item["options"] = {"parsed_rule": {"execution_plan": plan}, "rule_parser": {"source_summary": item["rule_summary"]}}
        self.assertEqual(dimension_year(item, 2025), 2024)
        self.assertTrue(rule_readiness(item)["executable"])
        plan["inputs"][0]["year"] += 1
        self.assertFalse(rule_readiness(item)["executable"])

    def test_missing_rule_year_is_not_substituted_with_another_year(self):
        item = definition()["dimensions"][0]
        for headers in (["2024年收入"], ["2025年收入"], ["2022年收入", "2023年收入"]):
            with self.subTest(headers=headers):
                _, _, mapping = match_model_workbook(BytesIO(workbook(headers)), [item], 2025)
                if "2024年收入" not in headers:
                    self.assertIsNone(mapping["fields"].get("revenue:2024"))
                if "2023年收入" not in headers:
                    self.assertIsNone(mapping["fields"].get("revenue:2023"))
                self.assertNotIn("revenue:2025", mapping["fields"])

    def test_relative_headers_use_rule_year_not_batch_year(self):
        item = definition()["dimensions"][0]
        _, _, mapping = match_model_workbook(BytesIO(workbook(["上年收入", "当年收入"])), [item], 2025)
        self.assertEqual(mapping["fields"], {"revenue:2023": "B", "revenue:2024": "C"})

    def test_annual_sheet_can_include_previous_year_base(self):
        item = definition()["dimensions"][0]
        for headers in (["2023年收入", "2024年收入"], ["上年收入", "当年收入"]):
            with self.subTest(headers=headers):
                _, _, mapping = match_model_workbook(BytesIO(workbook(headers, "2024年收入")), [item], 2025)
                self.assertEqual(mapping["fields"], {"revenue:2023": "B", "revenue:2024": "C"})

    def test_sheet_year_cannot_override_explicit_column_year(self):
        item = definition(2025, "quality")["dimensions"][0]
        _, _, mapping = match_model_workbook(BytesIO(workbook(["2025年产品推广满意度"], "2024年品质服务")), [item], 2025)
        self.assertIsNone(mapping["fields"].get("quality:2025"))

    def test_mixed_indicator_years_map_to_their_own_fields(self):
        first = definition(2024)["dimensions"][0]
        second = definition(2025, "gross_profit")["dimensions"][0]
        headers = ["2023年收入", "2024年收入", "2024年毛利", "2025年毛利"]
        targets, _, mapping = match_model_workbook(BytesIO(workbook(headers)), [first, second], 2025)
        self.assertEqual(set(targets), {first["key"], second["key"]})
        self.assertEqual(mapping["fields"], {"revenue:2023": "B", "revenue:2024": "C", "gross_profit:2024": "D", "gross_profit:2025": "E"})

    def test_single_named_input_with_concrete_year_matches_without_extra_particle(self):
        item = definition(2024, "quality")["dimensions"][0]
        targets, _, mapping = match_model_workbook(BytesIO(workbook(["2024年产品推广满意度"])), [item], 2025)
        self.assertEqual(targets, [item["key"]])
        self.assertEqual(mapping["fields"], {"quality:2024": "B"})

    def test_parse_publish_upload_validate_calculate_and_export_rule_year(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, PRODUCT_EVAL_DATA_DIR=directory, GEMINI_API_KEY="", LLM_API_KEY=""):
            client = TestClient(app)
            model = definition()
            item = model["dimensions"][0]
            parsed = client.post("/api/rules/parse", json={"dimension_label": item["label"], "calculation_type": "revenue", "summary": item["rule_summary"]})
            self.assertEqual(parsed.status_code, 200, parsed.text)
            self.assertEqual(parsed.json()["provider"], "local")
            item["options"] = {"parsed_rule": parsed.json()["rule"], "rule_parser": {"source_summary": item["rule_summary"]}}
            draft = store.create_model("指定2024年收入", "RULE-2024", None)
            published = client.post(f"/api/models/{draft['id']}/publish", json={"definition": model})
            self.assertEqual(published.status_code, 200, published.text)
            run = client.post("/api/runs", json={"year": 2025, "name": "2025批次按2024规则", "model_id": draft["id"]}).json()
            prefix = f"/api/runs/{run['id']}"
            upload = client.post(prefix+"/files", files={"files": ("收入.xlsx", workbook(["2023年收入", "2024年收入", "2025年收入"]))})
            self.assertEqual(upload.status_code, 200, upload.text)
            self.assertEqual(upload.json()["files"][0]["mapping"]["fields"], {"revenue:2023": "B", "revenue:2024": "C"})
            validated = client.post(prefix+"/validate")
            self.assertEqual(validated.json()["validation"]["status"], "passed", validated.text)
            scored = client.post(prefix+"/calculate")
            self.assertEqual(scored.status_code, 200, scored.text)
            result = scored.json()
            self.assertEqual(result["traces"][0]["data_year"], 2024)
            self.assertEqual(result["traces"][0]["steps"][0]["source"]["field"], "revenue:2023")
            self.assertEqual(result["traces"][0]["steps"][1]["display_label"], "2024年收入")
            self.assertEqual(result["traces"][0]["steps"][2]["value"], 1)
            book = load_workbook(BytesIO(make_workbook(run, result)), data_only=True)
            headers = [cell.value for cell in next(book[name] for name in book.sheetnames if "效益贡献" in name)[2]]
            self.assertIn("2023年收入", headers)
            self.assertIn("2024年收入", headers)
            self.assertNotIn("2025年收入", headers)
            book.close()
            files = store.file_records(run["id"])
            again = calculate(2027, files, {}, model)
            self.assertEqual(again["scores"], result["scores"])
            self.assertEqual(again["traces"][0]["data_year"], 2024)

    def test_preset_migration_runs_once_and_preserves_saved_results(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, PRODUCT_EVAL_DATA_DIR=directory):
            published = store.list_models()[0]
            run = store.create_run(2025, "历史批次", published["id"])
            with store.connect() as db:
                db.execute("DELETE FROM system_meta WHERE key='concrete_preset_years_v1'")
                db.execute("UPDATE model_versions SET definition=? WHERE id=?", (json.dumps(default_model_definition()), published["id"]))
                db.execute("UPDATE runs SET result=? WHERE id=?", ('{"snapshot":"keep"}', run["id"]))
            after = store.get_model(published["id"])
            self.assertIn("2025年", after["definition"]["dimensions"][1]["rule_summary"])
            class Future(datetime):
                @classmethod
                def now(cls, tz=None):
                    return cls(2028, 1, 1, tzinfo=tz)
            with patch("post_evaluation.store.datetime", Future):
                self.assertEqual(store.get_model(published["id"])["definition"], after["definition"])
            with store.connect() as db:
                self.assertEqual(db.execute("SELECT result FROM runs WHERE id=?", (run["id"],)).fetchone()[0], '{"snapshot":"keep"}')
