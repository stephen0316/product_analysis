import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from post_evaluation.app import app
from post_evaluation import store


class ReportLifecycleTests(unittest.TestCase):
    def test_explicit_generation_snapshot_and_invalidation(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, PRODUCT_EVAL_DATA_DIR=directory):
            client = TestClient(app)
            run = store.create_run(2025, "报告生成验证")
            path = f"/api/runs/{run['id']}"
            self.assertEqual(client.post(path + "/report", json={}).status_code, 409)
            store.update_run_json(run["id"], "result", {"covered_points": 10})
            self.assertIsNone(store.get_run(run["id"])["report"])
            self.assertEqual(client.get(path + "/report.docx").status_code, 409)
            with patch("post_evaluation.app.make_report", return_value=b"report snapshot") as renderer:
                generated = client.post(path + "/report", json={})
                self.assertEqual(generated.status_code, 200, generated.text)
                self.assertIsNotNone(generated.json()["report"])
                self.assertEqual(renderer.call_args.args[0]["notes"], {"analysis": "", "recommendations": "", "review": ""})
                self.assertEqual(client.get(path + "/report.docx").content, b"report snapshot")
                self.assertEqual(renderer.call_count, 1)
                client.put(path + "/notes", json={"analysis": "新分析"})
                self.assertIsNone(store.get_run(run["id"])["report"])
                self.assertEqual(client.get(path + "/report.docx").status_code, 409)
                regenerated = client.post(path + "/report", json={"analysis": "新分析"})
                self.assertEqual(regenerated.status_code, 200)
                store.update_run_json(run["id"], "result", {"covered_points": 20})
                self.assertEqual(client.get(path + "/report.docx").status_code, 409)
                self.assertIsNone(store.get_run(run["id"])["report"])
