import unittest

from post_evaluation.constants import PRODUCTS
from post_evaluation.scoring import _score_economic, rank_scores


class RankingTests(unittest.TestCase):
    def test_competition_rank_skips_after_tie(self):
        ranks, scores = rank_scores({"A": 8, "B": 8, "C": 7})
        self.assertEqual(ranks, {"A": 1, "B": 1, "C": 3})
        self.assertEqual(scores, {"A": 10, "B": 10, "C": 9})

    def test_negative_values_still_rank_descending(self):
        ranks, scores = rank_scores({"A": -2, "B": -5, "C": 0})
        self.assertEqual(ranks, {"A": 2, "B": 3, "C": 1})
        self.assertEqual(scores["B"], 9)

    def test_zero_base_blocks_dimension_without_zero_score(self):
        selected = {"revenue:2024": {}, "revenue:2025": {}}
        for index, product in enumerate(PRODUCTS):
            selected["revenue:2024"][product] = {"value": 0 if index == 0 else 1}
            selected["revenue:2025"][product] = {"value": index + 2}
        scores, traces, status, issues = {p: {} for p in PRODUCTS}, [], {"revenue": "数据待核对"}, []
        _score_economic("revenue", 2025, selected, scores, traces, status, issues)
        self.assertEqual(status["revenue"], "数据待核对")
        self.assertEqual(traces, [])
        self.assertEqual(issues[0]["code"], "zero_base")

    def test_negative_base_uses_absolute_growth(self):
        selected = {"revenue:2024": {}, "revenue:2025": {}}
        for index, product in enumerate(PRODUCTS):
            previous = -2 if index == 0 else 1
            current = -1 if index == 0 else index + 2
            selected["revenue:2024"][product] = {"value": previous}
            selected["revenue:2025"][product] = {"value": current}
        scores, traces, status, issues = {p: {} for p in PRODUCTS}, [], {"revenue": "数据待核对"}, []
        _score_economic("revenue", 2025, selected, scores, traces, status, issues)
        self.assertEqual(status["revenue"], "已计算")
        self.assertEqual(next(t for t in traces if t["product"] == PRODUCTS[0])["steps"][2]["value"], 0.5)
        self.assertTrue(any(i["code"] == "negative_base" for i in issues))


if __name__ == "__main__":
    unittest.main()
