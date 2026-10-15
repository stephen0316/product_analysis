from copy import deepcopy
from io import BytesIO
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from openpyxl import Workbook

from post_evaluation import store
from post_evaluation.app import app


def workbook_bytes(value=10):
    book = Workbook()
    book.active.append(["产品", "2025年市场份额"])
    book.active.append(["千里眼", value])
    buffer = BytesIO()
    book.save(buffer)
    book.close()
    return buffer.getvalue()


class BatchUploadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        environment = patch.dict(os.environ, PRODUCT_EVAL_DATA_DIR=self.directory.name)
        environment.start()
        self.addCleanup(environment.stop)
        self.client = TestClient(app)
        self.run = store.create_run(2025, "批量上传验收")
        self.url = f"/api/runs/{self.run['id']}/files"
        self.valid = workbook_bytes()

    def upload(self, *entries):
        return self.client.post(self.url, files=[("files", (name, content)) for name, content in entries])

    def test_mixed_invalid_oversized_and_duplicate_files_continue_in_order(self):
        response = self.upload(
            ("有效.xlsx", self.valid), ("损坏.xlsx", b"not a workbook"),
            ("超限.xlsx", b"x" * (30 * 1024 * 1024 + 1)),
            ("同内容改名.xlsx", self.valid), ("非表格.txt", b"plain"),
            ("另一有效.xlsx", workbook_bytes(20)),
        )
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual([i["status"] for i in result["upload_results"]],
                         ["uploaded", "failed", "failed", "duplicate", "failed", "uploaded"])
        self.assertEqual([i["index"] for i in result["upload_results"]], list(range(6)))
        self.assertEqual(len(result["files"]), 2)
        self.assertEqual(result["upload_results"][3]["file_id"], result["files"][0]["id"])
        self.assertIn("有效.xlsx", result["upload_results"][3]["message"])
        self.assertEqual(len(store.file_records(self.run["id"])), 2)
        self.assertEqual(len(list((Path(self.directory.name) / "uploads" / self.run["id"]).glob("*.xlsx"))), 2)

    def test_failure_before_success_and_all_failed_have_per_file_results(self):
        result = self.upload(("坏.xlsx", b"bad"), ("好.xlsx", self.valid)).json()
        self.assertEqual([i["status"] for i in result["upload_results"]], ["failed", "uploaded"])
        self.assertEqual(len(result["files"]), 1)
        result = self.upload(("仍坏.xlsx", b"bad"), ("空.xlsx", b"")).json()
        self.assertEqual([i["status"] for i in result["upload_results"]], ["failed", "failed"])
        self.assertEqual(len(result["files"]), 1)

    def test_reupload_same_content_preserves_mapping_validation_and_results(self):
        first = self.upload(("原表.xlsx", self.valid)).json()
        with store.connect() as db:
            db.execute("UPDATE runs SET result=?,validation=? WHERE id=?", ('{"marker": "existing result"}', '{"status":"passed"}', self.run["id"]))
        before = deepcopy(store.get_run(self.run["id"]))
        result = self.upload(("改名后.xlsx", self.valid)).json()
        self.assertEqual(result["upload_results"][0]["status"], "duplicate")
        self.assertEqual(result["files"][0]["id"], first["files"][0]["id"])
        self.assertEqual(store.get_run(self.run["id"]), before)

    def test_retry_failed_save_does_not_repeat_successful_upload(self):
        actual_save = store.add_file
        def fail_one(*args, **kwargs):
            if args[3] == "暂时失败.xlsx":
                raise OSError("disk unavailable")
            return actual_save(*args, **kwargs)
        retry_bytes = workbook_bytes(20)
        with patch("post_evaluation.app.store.add_file", side_effect=fail_one):
            result = self.upload(("已成功.xlsx", self.valid), ("暂时失败.xlsx", retry_bytes)).json()
        self.assertEqual([i["status"] for i in result["upload_results"]], ["uploaded", "failed"])
        self.assertTrue(result["upload_results"][1]["retryable"])
        result = self.upload(("暂时失败.xlsx", retry_bytes)).json()
        self.assertEqual([i["status"] for i in result["upload_results"]], ["uploaded"])
        self.assertEqual(len(result["files"]), 2)
        # Lost-response retry of the original selection is also idempotent.
        repeated = self.upload(("已成功.xlsx", self.valid), ("暂时失败.xlsx", retry_bytes)).json()
        self.assertEqual([i["status"] for i in repeated["upload_results"]], ["duplicate", "duplicate"])
        self.assertEqual(len(repeated["files"]), 2)

    def test_same_name_different_content_is_not_silently_dropped(self):
        result = self.upload(("同名.xlsx", self.valid), ("同名.xlsx", workbook_bytes(20))).json()
        self.assertEqual([i["status"] for i in result["upload_results"]], ["uploaded", "uploaded"])
        self.assertNotEqual(result["files"][0]["sha256"], result["files"][1]["sha256"])

    def test_failed_database_save_removes_only_uncommitted_file(self):
        self.upload(("保留.xlsx", self.valid))
        before = list((Path(self.directory.name) / "uploads" / self.run["id"]).glob("*.xlsx"))
        with patch("post_evaluation.store.connect", side_effect=OSError("database unavailable")):
            with self.assertRaises(OSError):
                store.add_file(self.run["id"], "market", [], "失败.xlsx", workbook_bytes(30), {}, [])
        self.assertEqual(list((Path(self.directory.name) / "uploads" / self.run["id"]).glob("*.xlsx")), before)
        self.assertEqual(len(store.file_records(self.run["id"])), 1)
