import os
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from post_evaluation.app import app
from post_evaluation.constants import PRODUCTS
from post_evaluation.model_defaults import split_legacy_economic_definition
from post_evaluation import store


class ModelConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.previous = os.environ.get("PRODUCT_EVAL_DATA_DIR")
        os.environ["PRODUCT_EVAL_DATA_DIR"] = self.directory.name
        self.client = TestClient(app)

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("PRODUCT_EVAL_DATA_DIR", None)
        else:
            os.environ["PRODUCT_EVAL_DATA_DIR"] = self.previous
        self.directory.cleanup()

    def test_clone_edit_publish_and_lock(self):
        published = self.client.get("/api/models").json()[0]
        response = self.client.post("/api/models", json={
            "name": "2026 年重点产品评估模型",
            "version": "2026-V1",
            "clone_from": published["id"],
            "reason": "复制年度模型",
        })
        self.assertEqual(response.status_code, 200, response.text)
        draft = response.json()
        definition = draft["definition"]
        definition["dimensions"][0]["weight"] = 20
        invalid = self.client.put(f"/api/models/{draft['id']}", json={
            "name": draft["name"], "version": draft["version"],
            "definition": definition, "reason": "验证权重控制",
        })
        self.assertEqual(invalid.status_code, 200, invalid.text)
        blocked = self.client.post(f"/api/models/{draft['id']}/publish", json={"reason": "尝试发布"})
        self.assertEqual(blocked.status_code, 400)
        definition["dimensions"][0]["weight"] = 10
        saved = self.client.put(f"/api/models/{draft['id']}", json={
            "name": draft["name"], "version": draft["version"],
            "definition": definition, "reason": "市场权重提升，经济权重同步下调",
        })
        self.assertEqual(saved.status_code, 200, saved.text)
        released = self.client.post(f"/api/models/{draft['id']}/publish", json={"reason": "评审通过"})
        self.assertEqual(released.status_code, 200, released.text)
        self.assertEqual(released.json()["status"], "published")
        locked = self.client.put(f"/api/models/{draft['id']}", json={
            "name": draft["name"], "version": draft["version"],
            "definition": definition, "reason": "不应成功",
        })
        self.assertEqual(locked.status_code, 409)
        events = self.client.get(f"/api/models/{draft['id']}").json()["events"]
        self.assertTrue({"clone", "update", "publish"}.issubset({event["event_type"] for event in events}))

    def test_economic_dimensions_are_independently_configurable(self):
        model = self.client.get("/api/models").json()[0]
        dimensions = model["definition"]["dimensions"]
        economic = [item for item in dimensions if item.get("group") == "economic"]
        self.assertEqual(len(dimensions), 10)
        self.assertEqual(len(economic), 5)
        self.assertEqual([item["metric_keys"] for item in economic], [
            ["revenue"], ["gross_profit"], ["gross_margin"],
            ["operating_profit"], ["operating_margin"],
        ])
        self.assertEqual([item["weight"] for item in economic], [10.0] * 5)

    def test_legacy_economic_dimension_migrates_without_weight_change(self):
        legacy = {"dimensions": [{
            "key": "economic", "label": "经济效益", "enabled": True, "weight": 50,
            "aggregation": "average_scaled",
            "metric_keys": ["revenue", "gross_profit", "gross_margin", "operating_profit", "operating_margin"],
            "options": {"growth_absolute": True},
        }]}
        definition, migrated = split_legacy_economic_definition(legacy)
        self.assertTrue(migrated)
        self.assertEqual(len(definition["dimensions"]), 5)
        self.assertEqual(sum(item["weight"] for item in definition["dimensions"]), 50)

    def test_delete_model_obeys_status_and_reference_protection(self):
        published = self.client.get("/api/models").json()[0]
        protected = self.client.delete(f"/api/models/{published['id']}")
        self.assertEqual(protected.status_code, 409)

        draft = self.client.post("/api/models", json={
            "name": "可删除草稿", "version": "DRAFT-DELETE",
            "clone_from": published["id"], "reason": "测试删除",
        }).json()
        deleted = self.client.delete(f"/api/models/{draft['id']}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(self.client.get(f"/api/models/{draft['id']}").status_code, 404)

        run = self.client.post("/api/runs", json={"year": 2025, "name": "引用保护", "model_id": published["id"]})
        self.assertEqual(run.status_code, 200, run.text)
        replacement = self.client.post("/api/models", json={
            "name": "替代版本", "version": "2025-V2",
            "clone_from": published["id"], "reason": "测试归档保护",
        }).json()
        released = self.client.post(f"/api/models/{replacement['id']}/publish", json={"reason": "测试替代"})
        self.assertEqual(released.status_code, 200, released.text)
        referenced = self.client.delete(f"/api/models/{published['id']}")
        self.assertEqual(referenced.status_code, 409)
        self.assertIn("评估批次引用", referenced.json()["detail"])

    def test_dimension_can_be_removed_without_enabled_toggle(self):
        published = self.client.get("/api/models").json()[0]
        draft = self.client.post("/api/models", json={
            "name": "删除维度草稿", "version": "DRAFT-DIMENSION",
            "clone_from": published["id"], "reason": "测试维度删除",
        }).json()
        definition = draft["definition"]
        removed = definition["dimensions"].pop()
        saved = self.client.put(f"/api/models/{draft['id']}", json={
            "name": draft["name"], "version": draft["version"],
            "definition": definition, "reason": f"删除{removed['label']}",
        })
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(len(saved.json()["definition"]["dimensions"]), 9)
        self.assertEqual(sum(item["weight"] for item in saved.json()["definition"]["dimensions"]), 90)

    def test_delete_run_removes_record_and_upload_directory(self):
        model = self.client.get("/api/models").json()[0]
        run = self.client.post("/api/runs", json={"year": 2025, "name": "待删除批次", "model_id": model["id"]}).json()
        directory = Path(self.directory.name) / "uploads" / run["id"]
        directory.mkdir(parents=True)
        (directory / "placeholder.xlsx").write_bytes(b"placeholder")
        deleted = self.client.delete(f"/api/runs/{run['id']}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(self.client.get(f"/api/runs/{run['id']}").status_code, 404)
        self.assertFalse(directory.exists())

    def test_run_binds_published_model_and_simulates_comparison(self):
        model = self.client.get("/api/models").json()[0]
        created = self.client.post("/api/runs", json={"year": 2025, "name": "模型模拟", "model_id": model["id"]})
        self.assertEqual(created.status_code, 200, created.text)
        run = created.json()
        self.assertEqual(run["model_id"], model["id"])
        scores = {}
        metric_keys = ["market_share", "revenue", "gross_profit", "gross_margin", "operating_profit", "operating_margin", "three_eff", "self_research", "ability", "quality"]
        for index, product in enumerate(PRODUCTS):
            scores[product] = {key: 10 - index * 0.5 for key in metric_keys}
        store.update_run_json(run["id"], "result", {"scores": scores})
        response = self.client.post(f"/api/models/{model['id']}/simulate", json={"run_id": run["id"], "baseline_model_id": model["id"]})
        self.assertEqual(response.status_code, 200, response.text)
        simulation = response.json()["simulation"]
        self.assertTrue(simulation["ready"])
        self.assertEqual(simulation["complete_products"], len(PRODUCTS))
        self.assertEqual(simulation["rows"][0]["rank"], 1)
        self.assertEqual(simulation["rows"][0]["rank_delta"], 0)


if __name__ == "__main__":
    unittest.main()
