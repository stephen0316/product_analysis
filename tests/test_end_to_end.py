from io import BytesIO
import os
from pathlib import Path
import tempfile
import unittest

from docx import Document
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from post_evaluation.app import app
from post_evaluation.constants import PRODUCTS


SOURCE_DIR = Path("/Users/apple/Desktop/后评估")
SOURCES = (
    ("market", "产品市场份额信息统计0113.xlsx"),
    ("finance", "产品后评估-财务部-原表.xlsx"),
    ("three_eff", "20260728-2526产品线三效数据-发市场部（定稿）-补充2506三效.xlsx"),
)


@unittest.skipUnless(all((SOURCE_DIR / name).exists() for _, name in SOURCES), "本机样本附件不可用")
class EndToEndTests(unittest.TestCase):
    def test_upload_calculate_and_export(self):
        with tempfile.TemporaryDirectory() as directory:
            old = os.environ.get("PRODUCT_EVAL_DATA_DIR")
            os.environ["PRODUCT_EVAL_DATA_DIR"] = directory
            try:
                client = TestClient(app)
                created = client.post("/api/runs", json={"year": 2025, "name": "自动化验证"})
                self.assertEqual(created.status_code, 200)
                run_id = created.json()["id"]
                for role, name in SOURCES:
                    with (SOURCE_DIR / name).open("rb") as source:
                        response = client.post(f"/api/runs/{run_id}/files", data={"role": role}, files={"file": (name, source, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertIsNotNone(response.json()["files"][-1]["mapping"]["sheet"])
                response = client.post(f"/api/runs/{run_id}/calculate")
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                self.assertEqual(result["covered_points"], 70)
                self.assertFalse(result["official_total_available"])
                self.assertEqual(len(result["traces"]), len(PRODUCTS) * 7)
                self.assertFalse([i for i in result["issues"] if i["level"] == "error"])
                self.assertEqual([m["status"] for m in result["metrics"]][-3:], ["待完成"] * 3)
                self.assertTrue(all(len(result["scores"][p]) == 7 for p in PRODUCTS))

                workbook_response = client.get(f"/api/runs/{run_id}/export.xlsx")
                self.assertEqual(workbook_response.status_code, 200)
                wb = load_workbook(BytesIO(workbook_response.content))
                self.assertEqual(wb.sheetnames, ["重点产品-总分", "计算明细", "校验与待确认", "数据来源"])
                ws = wb["重点产品-总分"]
                self.assertEqual(ws["C3"].value, result["scores"]["OnePark"]["market_share"])
                self.assertIsNone(ws["L3"].value)
                self.assertIsNone(ws["M3"].value)
                self.assertIsNone(ws["N3"].value)

                report_response = client.get(f"/api/runs/{run_id}/report.docx")
                self.assertEqual(report_response.status_code, 200)
                document = Document(BytesIO(report_response.content))
                text = "\n".join(p.text for p in document.paragraphs)
                self.assertIn("不生成正式总分", text)
                self.assertEqual(len(document.inline_shapes), 1)
            finally:
                if old is None:
                    os.environ.pop("PRODUCT_EVAL_DATA_DIR", None)
                else:
                    os.environ["PRODUCT_EVAL_DATA_DIR"] = old


if __name__ == "__main__":
    unittest.main()
