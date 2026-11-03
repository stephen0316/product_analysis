from copy import deepcopy
from io import BytesIO
import os
import tempfile
import unittest
from unittest.mock import patch

from auth_test_support import TestClient
from openpyxl import Workbook, load_workbook

from post_evaluation import store
from post_evaluation.app import app
from post_evaluation.calculation_types import calculation_types, compile_rule, local_rule, rule_readiness
from post_evaluation.constants import FIELD_LABELS, PRODUCTS, SELF_RESEARCH_COST_FIELDS, input_specs_for_dimension
from post_evaluation.cost_scope import DEFAULT_SELF_RESEARCH_SUMMARY, LEGACY_SELF_RESEARCH_SUMMARY, cost_scope
from post_evaluation.model_defaults import default_model_definition
from post_evaluation.model_mapping import match_model_workbook
from test_execution_boundary import calculate_sample, sample_records
from test_semantic_rules import example_plan

SELECTED = "research_cost_external_independent"


def definition(costs=(SELECTED,)):
    result = default_model_definition()
    item = next(d for d in result["dimensions"] if d["calculation_type"] == "self_research")
    item.update(weight=100, rule_summary=LEGACY_SELF_RESEARCH_SUMMARY + "研发总成本取：" + "、".join(FIELD_LABELS[k] for k in costs) + "。")
    result["dimensions"] = [item]
    return result


def workbook(costs=(SELECTED,), *, bad_extra=False, zero=False):
    book = Workbook()
    ws = book.active
    ws.title = "研发数据"
    ws.append(["产品线", "自研收入占比", "研发收入", *[FIELD_LABELS[k] for k in costs], *([FIELD_LABELS["research_cost_travel"]] if bad_extra else [])])
    for index, product in enumerate(PRODUCTS):
        ws.append([product, .8-index*.02, 100-index, *[0 if zero else 2 for _ in costs], *(["不应参与校验"] if bad_extra else [])])
    buffer = BytesIO()
    book.save(buffer)
    book.close()
    return buffer.getvalue()


class CostScopeTests(unittest.TestCase):
    def test_readonly_display_catalog_has_equivalent_legacy_summary(self):
        catalog = next(item for item in calculation_types() if item["id"] == "self_research")
        self.assertEqual(catalog["legacy_rule_summary"], LEGACY_SELF_RESEARCH_SUMMARY)
        self.assertEqual(cost_scope(catalog["rule_summary"])[0], cost_scope(catalog["legacy_rule_summary"])[0])
        self.assertFalse(any("legacy_rule_summary" in item for item in calculation_types() if item["id"] != "self_research"))

    def test_default_scope_is_explicit_and_legacy_calculation_unchanged(self):
        for key in SELF_RESEARCH_COST_FIELDS:
            self.assertIn(FIELD_LABELS[key], DEFAULT_SELF_RESEARCH_SUMMARY)
        baseline = definition(SELF_RESEARCH_COST_FIELDS)
        legacy = deepcopy(baseline)
        legacy["dimensions"][0]["rule_summary"] = LEGACY_SELF_RESEARCH_SUMMARY
        self.assertEqual(calculate_sample(baseline)["scores"], calculate_sample(legacy)["scores"])

    def test_selected_costs_drive_local_rule_contract_and_readiness(self):
        item = definition()["dimensions"][0]
        rule = local_rule("self_research", item["rule_summary"])
        self.assertEqual(len(rule["required_inputs"]), 3)
        self.assertEqual(compile_rule(item, "self_research")["research_cost_fields"], [SELECTED])
        self.assertEqual([s["key"] for s in input_specs_for_dimension(item, 2026)],
                         ["self_owned_ratio:2026", "research_revenue:2026", f"{SELECTED}:2026"])
        self.assertEqual(rule_readiness(item)["inputs"][-1], "评估年度研发总成本（按摘要所列范围）")

    def test_one_two_and_seven_cost_scopes_accept_fullwidth_and_halfwidth_names(self):
        for keys in ([SELECTED], [SELECTED, "research_cost_travel"], SELF_RESEARCH_COST_FIELDS):
            summary = definition(keys)["dimensions"][0]["rule_summary"].replace("（", "(").replace("）", ")")
            self.assertEqual(cost_scope(summary)[0], [k for k in SELF_RESEARCH_COST_FIELDS if k in keys])

    def test_unknown_excluded_or_duplicate_scope_does_not_restore_defaults(self):
        for suffix in ("研发总成本取：研究开发费-未知成本。", "研发总成本取：研究开发费-差旅费，不含研究开发费-合作人工。",
                       "研发总成本取：研究开发费-差旅费。研发总成本取：研究开发费-合作人工。", "研发总成本取：人工成本。", "研发总成本取：。"):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                cost_scope(LEGACY_SELF_RESEARCH_SUMMARY + suffix)

    def test_typed_plan_supports_subset_and_rejects_summary_mismatch(self):
        item = definition()["dimensions"][0]
        plan = example_plan("self_research")
        plan["inputs"] = [i for i in plan["inputs"] if not i["field"].startswith("research_cost_") or i["field"] == SELECTED]
        item["options"] = {"parsed_rule": {"execution_plan": plan}, "rule_parser": {"source_summary": item["rule_summary"]}}
        self.assertTrue(rule_readiness(item)["executable"])
        item["options"]["parsed_rule"]["execution_plan"] = example_plan("self_research")
        self.assertIn("成本范围不一致", rule_readiness(item)["errors"][0])

    def test_missing_unselected_costs_do_not_block_or_enter_denominator(self):
        records = [r for r in sample_records() if not r["field"].startswith("research_cost_") or r["field"] == SELECTED+":2025"]
        result = calculate_sample(definition(), records)
        self.assertFalse(result["issues"])
        trace = result["traces"][0]
        cost = next(s for s in trace["steps"] if s["label"] == "研发总成本")
        self.assertEqual(cost["value"], 1)
        self.assertEqual(cost["formula"], FIELD_LABELS[SELECTED])
        self.assertEqual(next(s for s in trace["steps"] if s["label"] == "研发投入产出比")["value"], 100)

    def test_missing_selected_cost_still_blocks_this_indicator(self):
        records = [r for r in sample_records() if r["field"] != SELECTED+":2025"]
        result = calculate_sample(definition(), records)
        self.assertEqual(len([i for i in result["issues"] if i["code"] == "missing"]), len(PRODUCTS))
        self.assertFalse(result["traces"])

    def test_zero_selected_cost_remains_a_zero_denominator_error(self):
        records = sample_records()
        for record in records:
            if record["field"] == SELECTED+":2025":
                record["value"] = 0
        result = calculate_sample(definition(), records)
        self.assertEqual(len([i for i in result["issues"] if i["code"] == "zero_cost"]), len(PRODUCTS))
        self.assertFalse(result["traces"])

    def test_two_selected_costs_are_summed_not_replaced_with_all_seven(self):
        result = calculate_sample(definition([SELECTED, "research_cost_travel"]))
        cost = next(s for s in result["traces"][0]["steps"] if s["label"] == "研发总成本")
        self.assertEqual(cost["value"], 2)
        self.assertIn(FIELD_LABELS[SELECTED], cost["formula"])
        self.assertIn(FIELD_LABELS["research_cost_travel"], cost["formula"])

    def test_mapping_discards_unselected_cost_columns(self):
        item = definition()["dimensions"][0]
        targets, _, mapping = match_model_workbook(BytesIO(workbook(bad_extra=True)), [item], 2025)
        self.assertEqual(targets, [item["key"]])
        self.assertEqual(mapping["fields"][SELECTED+":2025"], "D")
        self.assertNotIn("research_cost_travel:2025", mapping["fields"])

    def test_parse_publish_upload_validate_calculate_and_export_subset(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, PRODUCT_EVAL_DATA_DIR=directory, GEMINI_API_KEY="", LLM_API_KEY=""):
            client = TestClient(app)
            item = definition()["dimensions"][0]
            parsed = client.post("/api/rules/parse", json={"dimension_label": item["label"], "calculation_type": "self_research", "summary": item["rule_summary"]})
            self.assertEqual(parsed.status_code, 200, parsed.text)
            self.assertEqual(parsed.json()["provider"], "local")
            draft = store.create_model("单成本范围", "COST-1", None)
            model = definition()
            model["dimensions"][0]["options"] = {"parsed_rule": parsed.json()["rule"], "rule_parser": {"source_summary": item["rule_summary"]}}
            response = client.post(f"/api/models/{draft['id']}/publish", json={"definition": model})
            self.assertEqual(response.status_code, 200, response.text)
            run = client.post("/api/runs", json={"year": 2025, "name": "仅取外采", "model_id": draft["id"]}).json()
            prefix = f"/api/runs/{run['id']}"
            uploaded = client.post(prefix+"/files", files={"files": ("研发原表.xlsx", workbook(bad_extra=True))})
            self.assertEqual(uploaded.status_code, 200, uploaded.text)
            self.assertNotIn("research_cost_travel:2025", uploaded.json()["files"][0]["mapping"]["fields"])
            validated = client.post(prefix+"/validate")
            self.assertEqual(validated.json()["validation"]["status"], "passed", validated.text)
            scored = client.post(prefix+"/calculate")
            self.assertEqual(scored.status_code, 200, scored.text)
            self.assertFalse(scored.json()["issues"])
            self.assertEqual(next(s for s in scored.json()["traces"][0]["steps"] if s["label"] == "研发总成本")["value"], 2)
            from post_evaluation.exports import make_workbook
            book = load_workbook(BytesIO(make_workbook(run, scored.json())), data_only=True)
            sheet = next(book[name] for name in book.sheetnames if "自研能力" in name)
            rows = list(sheet.values)
            header = next(row for row in rows if "研发总成本" in row)
            first = next(row for row in rows if PRODUCTS[0] in row)
            self.assertEqual(first[header.index("研发总成本")], 2)
            self.assertEqual(first[header.index("研发投入产出比")], 50)
            self.assertFalse(any(FIELD_LABELS[k] in str(header) for k in SELF_RESEARCH_COST_FIELDS))
            book.close()

    def test_editable_draft_expands_legacy_default_without_changing_published(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, PRODUCT_EVAL_DATA_DIR=directory):
            original = store.list_models()[0]
            legacy = deepcopy(original["definition"])
            item = next(i for i in legacy["dimensions"] if i["calculation_type"] == "self_research")
            item["rule_summary"] = LEGACY_SELF_RESEARCH_SUMMARY
            import json
            with store.connect() as db:
                db.execute("UPDATE model_versions SET definition=? WHERE id=?", (json.dumps(legacy), original["id"]))
            clone = store.create_model("兼容草稿", "COMPAT", original["id"])
            self.assertIn("研发总成本取：", next(i for i in clone["definition"]["dimensions"] if i["calculation_type"] == "self_research")["rule_summary"])
            self.assertEqual(store.get_model(original["id"])["definition"], legacy)
