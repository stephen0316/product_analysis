import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from post_evaluation import store
from post_evaluation.app import app
from post_evaluation.constants import PRODUCTS
from post_evaluation.model_defaults import default_model_definition
from post_evaluation.weight_optimizer import optimize_weights


METRIC_KEYS = [
    "market_share", "revenue", "gross_profit", "gross_margin", "operating_profit",
    "operating_margin", "three_eff", "self_research", "ability", "quality",
]


def sample_result():
    scores = {product: {key: 5.0 for key in METRIC_KEYS} for product in PRODUCTS}
    scores["千里眼"]["market_share"] = 10.0
    scores["千里眼"]["revenue"] = 0.0
    return {"scores": scores}


class WeightOptimizerTests(unittest.TestCase):
    def test_finds_auditable_weight_plan_without_changing_total_weight(self):
        result = optimize_weights(
            default_model_definition(),
            sample_result(),
            {"target_product": "千里眼", "direction": "down", "positions": 3, "normalized_goal": "让千里眼排名后移三位"},
            max_weight_change=5,
            max_other_rank_change=2,
        )

        self.assertTrue(result["feasible"])
        self.assertGreaterEqual(len(result["candidates"]), 1)
        candidate = result["candidates"][0]
        self.assertTrue(candidate["achieved"])
        self.assertEqual(candidate["target_rank_before"], 1)
        self.assertGreaterEqual(candidate["target_rank_after"], 4)
        self.assertAlmostEqual(sum(candidate["weights"].values()), 100)
        self.assertTrue(all(abs(item["delta"]) <= 5 for item in candidate["weight_changes"]))
        self.assertEqual(len(candidate["rows"]), len(PRODUCTS))

    def test_rejects_incomplete_batch_scores(self):
        result = sample_result()
        del result["scores"]["OnePark"]["quality"]
        with self.assertRaisesRegex(ValueError, "评分数据不完整"):
            optimize_weights(
                default_model_definition(), result,
                {"target_product": "千里眼", "direction": "down", "positions": 3},
            )


class WeightOptimizerApiTests(unittest.TestCase):
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

    def test_endpoint_parses_goal_then_runs_deterministic_optimizer(self):
        model = self.client.get("/api/models").json()[0]
        run = self.client.post("/api/runs", json={
            "year": 2025,
            "name": "智能调优测试",
            "model_id": model["id"],
        }).json()
        store.update_run_json(run["id"], "result", sample_result())
        interpretation = {
            "goal": {
                "target_product": "千里眼", "direction": "down", "positions": 3,
                "normalized_goal": "让千里眼排名后移三位", "assumptions": [], "warnings": [], "confidence": 1,
            },
            "provider": "gemini", "model": "test", "parsed_at": "2026-10-14T00:00:00+00:00",
        }
        with patch("post_evaluation.app.parse_weight_goal", return_value=interpretation):
            response = self.client.post(f"/api/models/{model['id']}/optimize-weights", json={
                "run_id": run["id"],
                "objective": "让千里眼排名后移三位",
                "max_weight_change": 5,
                "max_other_rank_change": 2,
            })

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["interpretation"]["provider"], "gemini")
        self.assertTrue(body["optimization"]["feasible"])
        self.assertEqual(body["optimization"]["goal"]["target_product"], "千里眼")


if __name__ == "__main__":
    unittest.main()
