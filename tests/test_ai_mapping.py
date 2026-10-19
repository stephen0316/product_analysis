from copy import deepcopy
from io import BytesIO
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from openpyxl import Workbook

from post_evaluation import store
from post_evaluation.app import app
from post_evaluation.ai_mapping import mapping_with_fallback, header_inventory
from post_evaluation.model_defaults import default_model_definition
from post_evaluation.model_mapping import match_model_workbook


def workbook(header="客户体验评价", sheet="原表"):
    book = Workbook()
    book.active.title = sheet
    book.active.append(["产品线", header])
    book.active.append(["不应发送的产品名称", 98.765])
    result = BytesIO()
    book.save(result)
    book.close()
    return result.getvalue()


class AIMappingTests(unittest.TestCase):
    def setUp(self):
        self.dims = [deepcopy(default_model_definition()["dimensions"][-1])]
        self.content = workbook()
        self.proposal = {"targets": ["quality"], "sheet": "原表", "header_row": 1,
                         "product_col": "A", "fields": [{"field": "quality:2025", "column": "B"}],
                         "confidence": .96, "reason": "客户体验评价是服务满意度原始评分", "questions": []}
        self.environment = patch.dict(os.environ, GEMINI_API_KEY="test-key")
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def fallback(self, content=None, result=None):
        content = content or self.content
        return mapping_with_fallback(BytesIO(content), "测试.xlsx", self.dims, 2025,
                    result or match_model_workbook(BytesIO(content), self.dims, 2025))

    def test_rule_success_never_calls_ai(self):
        with patch("post_evaluation.ai_mapping.request_mapping") as ai:
            targets, _, mapping = self.fallback(workbook("产品推广满意度"))
        ai.assert_not_called()
        self.assertEqual(targets, ["quality"])
        self.assertEqual(mapping["mapping_method"], "rules")

    def test_ai_fallback_uses_only_headers_and_preserves_provenance(self):
        with patch("post_evaluation.ai_mapping.request_mapping", return_value=self.proposal) as ai:
            targets, _, mapping = self.fallback()
        self.assertEqual(ai.call_count, 1)
        self.assertNotIn("不应发送的产品名称", str(ai.call_args))
        self.assertNotIn("98.765", str(ai.call_args))
        self.assertEqual(targets, ["quality"])
        self.assertEqual(mapping["fields"]["quality:2025"], "B")
        self.assertEqual(mapping["mapping_method"], "ai")

    def test_missing_configuration_and_provider_failure_require_manual(self):
        with patch.dict(os.environ, GEMINI_API_KEY=""), patch("post_evaluation.ai_mapping.request_mapping") as ai:
            self.assertEqual(self.fallback()[2]["mapping_method"], "manual")
            ai.assert_not_called()
        with patch("post_evaluation.ai_mapping.request_mapping", side_effect=TimeoutError):
            mapping = self.fallback()[2]
        self.assertEqual(mapping["mapping_method"], "manual")
        self.assertTrue(mapping["ai_attempted"])

    def test_invalid_uncertain_or_overwriting_proposals_are_not_applied(self):
        for change in ({"confidence": .8}, {"confidence": float("nan")}, {"questions": ["口径不明"]},
                       {"sheet": "编造表"}, {"header_row": 5}, {"product_col": "Z"},
                       {"targets": ["invented"]}, {"fields": [{"field": "quality:2025", "column": "Z"}]},
                       {"fields": [{"field": "income:2025", "column": "B"}]}):
            with self.subTest(change=change), patch("post_evaluation.ai_mapping.request_mapping", return_value={**self.proposal, **change}):
                self.assertEqual(self.fallback()[2]["mapping_method"], "manual")
        original = (["quality"], ["quality"], {"sheet": "原表", "header_row": 1, "product_col": "A",
                    "fields": {"quality:2025": "C"}, "confirmation_note": "另一字段不明确"})
        with patch("post_evaluation.ai_mapping.request_mapping", return_value=self.proposal):
            mapping = self.fallback(result=original)[2]
        self.assertEqual(mapping["fields"]["quality:2025"], "C")
        self.assertEqual(mapping["mapping_method"], "manual")

    def test_wrong_year_and_derived_values_require_manual(self):
        for header in ("2024年客户评价", "客户评价排名", "客户评价增幅"):
            with self.subTest(header=header), patch("post_evaluation.ai_mapping.request_mapping", return_value=self.proposal):
                self.assertEqual(self.fallback(workbook(header))[2]["mapping_method"], "manual")

    def test_upload_ai_failure_still_saves_file_and_duplicate_skips_ai(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, PRODUCT_EVAL_DATA_DIR=directory):
            client = TestClient(app)
            run = store.create_run(2025, "AI兜底测试")
            url = f"/api/runs/{run['id']}/files"
            with patch("post_evaluation.ai_mapping.request_mapping", side_effect=TimeoutError) as ai:
                response = client.post(url, files={"files": ("测试.xlsx", self.content)}).json()
                self.assertEqual(response["upload_results"][0]["status"], "uploaded")
                self.assertEqual(response["files"][0]["mapping"]["mapping_method"], "manual")
                duplicate = client.post(url, files={"files": ("测试.xlsx", self.content)}).json()
                self.assertEqual(duplicate["upload_results"][0]["status"], "duplicate")
                self.assertEqual(ai.call_count, 1)

    def test_inventory_never_includes_data_rows(self):
        self.assertEqual(header_inventory(BytesIO(self.content)), [{"sheet": "原表", "header_row": 1,
                         "columns": {"A": "产品线", "B": "客户体验评价"}}])
