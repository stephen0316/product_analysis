import os
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from post_evaluation.app import app
from post_evaluation.constants import PRODUCTS
from post_evaluation.model_defaults import DIMENSION_CATEGORIES, ECONOMIC_RULE_SUMMARY, ensure_economic_growth_formula, ensure_indicator_categories, split_legacy_economic_definition
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

    def test_publish_applies_current_edits_and_keeps_other_models_published(self):
        original = self.client.get("/api/models").json()[0]
        draft = self.client.post("/api/models", json={
            "name": "待修改模型", "version": "2026-DRAFT",
            "clone_from": original["id"],
        }).json()
        definition = draft["definition"]
        definition["categories"][0]["label"] = "销售规模"

        released = self.client.post(f"/api/models/{draft['id']}/publish", json={
            "name": "2026 年销售评估模型",
            "version": "2026-V1",
            "definition": definition,
        })
        self.assertEqual(released.status_code, 200, released.text)
        released_model = released.json()
        self.assertEqual(released_model["name"], "2026 年销售评估模型")
        self.assertEqual(released_model["version"], "2026-V1")
        self.assertEqual(released_model["definition"]["categories"][0]["label"], "销售规模")

        models = self.client.get("/api/models").json()
        published_ids = {item["id"] for item in models if item["status"] == "published"}
        self.assertEqual(published_ids, {original["id"], draft["id"]})

    def test_legacy_auto_archived_models_are_restored_once(self):
        original = self.client.get("/api/models").json()[0]
        with store.connect() as db:
            db.execute("UPDATE model_versions SET status='archived' WHERE id=?", (original["id"],))
            db.execute("DELETE FROM system_meta WHERE key='multiple_published_models_v1'")

        restored = self.client.get(f"/api/models/{original['id']}").json()
        self.assertEqual(restored["status"], "published")
        self.assertEqual(restored["events"][0]["event_type"], "restore_publish")

    def test_economic_dimensions_are_independently_configurable(self):
        model = self.client.get("/api/models").json()[0]
        self.assertEqual(
            [(item["key"], item["label"]) for item in model["definition"]["categories"]],
            list(DIMENSION_CATEGORIES),
        )
        dimensions = model["definition"]["dimensions"]
        economic = [item for item in dimensions if item.get("group") == "economic"]
        self.assertEqual(len(dimensions), 10)
        self.assertEqual(len(economic), 5)
        self.assertEqual([item["metric_keys"] for item in economic], [
            ["revenue"], ["gross_profit"], ["gross_margin"],
            ["operating_profit"], ["operating_margin"],
        ])
        self.assertEqual([item["weight"] for item in economic], [10.0] * 5)
        self.assertTrue(all(item.get("category_key") for item in dimensions))
        self.assertTrue(all(item["category_key"] == "benefit_contribution" for item in economic))

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

    def test_legacy_growth_formula_migrates_to_absolute_denominator(self):
        legacy = {"dimensions": [{
            "key": "economic_revenue", "group": "economic", "metric_keys": ["revenue"],
            "rule_summary": "当年值与绝对增幅分别赋分，取平均后再次排名赋分。",
            "options": {"growth_absolute": True, "parsed_rule": {"steps": []}, "rule_parser": {"source_summary": "旧摘要"}},
        }]}
        definition, migrated = ensure_economic_growth_formula(legacy)
        self.assertTrue(migrated)
        indicator = definition["dimensions"][0]
        self.assertEqual(indicator["rule_summary"], ECONOMIC_RULE_SUMMARY)
        self.assertEqual(indicator["options"], {"growth_denominator_absolute": True})

    def test_legacy_indicators_receive_parent_categories(self):
        legacy = {"ranking": {"method": "competition", "first_score": 10, "decrement": .5}, "dimensions": [
            {"key": "market", "label": "市场份额"},
            {"key": "economic_revenue", "group": "economic", "label": "收入／增幅"},
            {"key": "quality", "label": "品质服务"},
        ]}
        definition, migrated = ensure_indicator_categories(legacy)
        self.assertTrue(migrated)
        self.assertEqual(len(definition["categories"]), 5)
        self.assertEqual(
            [item["category_key"] for item in definition["dimensions"]],
            ["market_share", "benefit_contribution", "quality_service"],
        )

    def test_indicator_requires_a_parent_category(self):
        published = self.client.get("/api/models").json()[0]
        draft = self.client.post("/api/models", json={
            "name": "归属维度校验", "version": "DRAFT-CATEGORY",
            "clone_from": published["id"], "reason": "测试归属维度",
        }).json()
        definition = draft["definition"]
        definition["dimensions"][0].pop("category_key")
        response = self.client.put(f"/api/models/{draft['id']}", json={
            "name": draft["name"], "version": draft["version"],
            "definition": definition, "reason": "移除归属维度",
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("必须选择归属维度", response.json()["detail"])

    def test_dimension_categories_can_be_renamed_and_added(self):
        published = self.client.get("/api/models").json()[0]
        draft = self.client.post("/api/models", json={
            "name": "可编辑维度模型", "version": "DRAFT-EDIT-CATEGORY",
            "clone_from": published["id"], "reason": "测试维度管理",
        }).json()
        definition = draft["definition"]
        definition["categories"][0]["label"] = "市场表现"
        definition["categories"].append({"key": "customer_value", "label": "客户价值"})
        saved = self.client.put(f"/api/models/{draft['id']}", json={
            "name": draft["name"], "version": draft["version"],
            "definition": definition, "reason": "调整维度",
        })
        self.assertEqual(saved.status_code, 200, saved.text)
        categories = saved.json()["definition"]["categories"]
        self.assertEqual(categories[0]["label"], "市场表现")
        self.assertEqual(categories[-1], {"key": "customer_value", "label": "客户价值"})

    def test_draft_category_changes_do_not_mutate_published_version(self):
        published = self.client.get("/api/models").json()[0]
        published_id = published["id"]
        original_categories = published["definition"]["categories"]
        original_dimensions = published["definition"]["dimensions"]
        draft = self.client.post("/api/models", json={
            "name": "独立草稿", "version": "DRAFT-INDEPENDENT-CATEGORIES",
            "clone_from": published_id,
        }).json()
        definition = draft["definition"]
        definition["categories"][0]["label"] = "销售规模"

        removed_category = definition["categories"].pop()
        target_category = definition["categories"][0]
        migrated_labels = []
        for indicator in definition["dimensions"]:
            if indicator["category_key"] == removed_category["key"]:
                indicator["category_key"] = target_category["key"]
                migrated_labels.append(indicator["label"])

        saved = self.client.put(f"/api/models/{draft['id']}", json={
            "name": draft["name"], "version": draft["version"], "definition": definition,
        })
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["definition"]["categories"][0]["label"], "销售规模")
        self.assertTrue(migrated_labels)
        events = self.client.get(f"/api/models/{draft['id']}").json()["events"]
        summary = events[0]["detail"]["summary"]
        self.assertIn("将市场份额维度重命名为销售规模", summary)
        self.assertIn(f"迁移至{target_category['label']}维度", summary)
        self.assertIn(f"删除{removed_category['label']}维度", summary)

        unchanged = self.client.get(f"/api/models/{published_id}").json()
        self.assertEqual(unchanged["definition"]["categories"], original_categories)
        self.assertEqual(unchanged["definition"]["dimensions"], original_dimensions)
        self.assertEqual(unchanged["status"], "published")

    def test_delete_model_obeys_status_and_reference_protection(self):
        published = self.client.get("/api/models").json()[0]
        protected = self.client.delete(f"/api/models/{published['id']}")
        self.assertEqual(protected.status_code, 409)
        self.assertIn("至少需要保留一个已发布模型", protected.json()["detail"])

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
        models = self.client.get("/api/models").json()
        self.assertEqual({item["id"] for item in models if item["status"] == "published"}, {published["id"], replacement["id"]})
        referenced = self.client.delete(f"/api/models/{published['id']}")
        self.assertEqual(referenced.status_code, 409)
        self.assertIn("评估批次引用", referenced.json()["detail"])
        unreferenced = self.client.delete(f"/api/models/{replacement['id']}")
        self.assertEqual(unreferenced.status_code, 200, unreferenced.text)
        remaining = self.client.get("/api/models").json()
        self.assertEqual([item["id"] for item in remaining if item["status"] == "published"], [published["id"]])

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

    def test_change_summary_is_generated_from_indicator_diff(self):
        published = self.client.get("/api/models").json()[0]
        draft = self.client.post("/api/models", json={
            "name": "自动记录模型", "version": "DRAFT-AUTO-AUDIT",
            "clone_from": published["id"],
        }).json()
        definition = draft["definition"]
        removed = definition["dimensions"].pop()
        definition["dimensions"].append({
            "key": "market_size", "category_key": definition["categories"][0]["key"],
            "label": "市场规模", "enabled": True, "weight": removed["weight"],
            "aggregation": "average_scaled", "metric_keys": ["market_size"],
            "calculation_type": "market_share",
            "rule_summary": "取最新年度市场规模，降序排名赋分。",
        })
        saved = self.client.put(f"/api/models/{draft['id']}", json={
            "name": draft["name"], "version": draft["version"], "definition": definition,
        })
        self.assertEqual(saved.status_code, 200, saved.text)
        events = self.client.get(f"/api/models/{draft['id']}").json()["events"]
        self.assertEqual(events[0]["detail"]["summary"], f"增加市场规模指标，删除{removed['label']}指标")

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
        self.assertEqual(run["data_year"], 2025)
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

    def test_interim_execution_year_batches_migrate_to_evaluated_year(self):
        model = self.client.get("/api/models").json()[0]
        timestamp = store.now()
        with store.connect() as db:
            db.execute("DELETE FROM system_meta WHERE key='evaluation_year_is_data_year_v1'")
            db.execute(
                "INSERT INTO runs(id,year,data_year,name,created_at,updated_at,rule_version,model_id) VALUES(?,?,?,?,?,?,?,?)",
                ("interim-year-run", 2026, 2025, "2026 年重点产品后评估", timestamp, timestamp, model["version"], model["id"]),
            )

        migrated = store.get_run("interim-year-run")

        self.assertEqual(migrated["year"], 2025)
        self.assertEqual(migrated["data_year"], 2025)
        self.assertEqual(migrated["name"], "2025 年重点产品后评估")

    def test_calculation_requires_a_current_passing_validation(self):
        model = self.client.get("/api/models").json()[0]
        run = self.client.post("/api/runs", json={"year": 2025, "name": "校验门禁", "model_id": model["id"]}).json()

        blocked = self.client.post(f"/api/runs/{run['id']}/calculate")
        self.assertEqual(blocked.status_code, 409)

        validated = self.client.post(f"/api/runs/{run['id']}/validate")
        self.assertEqual(validated.status_code, 200, validated.text)
        validation = validated.json()["validation"]
        self.assertEqual(validation["status"], "failed")
        self.assertEqual(validation["issues"][0]["code"], "no_files")


if __name__ == "__main__":
    unittest.main()
