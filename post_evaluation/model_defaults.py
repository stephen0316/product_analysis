from __future__ import annotations

from copy import deepcopy


DEFAULT_MODEL_ID = "model-2025-v1"


ECONOMIC_DIMENSIONS = (
    ("economic_revenue", "收入／增幅", "revenue"),
    ("economic_gross_profit", "毛利／增幅", "gross_profit"),
    ("economic_gross_margin", "毛利率／增幅", "gross_margin"),
    ("economic_operating_profit", "营业利润／增幅", "operating_profit"),
    ("economic_operating_margin", "营业利润率／增幅", "operating_margin"),
)


def _economic_dimension(key: str, label: str, metric_key: str, weight: float = 10.0, enabled: bool = True) -> dict:
    return {
        "key": key,
        "group": "economic",
        "label": label,
        "enabled": enabled,
        "weight": weight,
        "aggregation": "average_scaled",
        "metric_keys": [metric_key],
        "rule_summary": "当年值与绝对增幅分别赋分，取平均后再次排名赋分。",
        "options": {"growth_absolute": True},
    }


DEFAULT_MODEL_DEFINITION = {
    "ranking": {
        "method": "competition",
        "first_score": 10.0,
        "decrement": 0.5,
    },
    "dimensions": [
        {
            "key": "market",
            "label": "市场份额",
            "enabled": True,
            "weight": 10.0,
            "aggregation": "average_scaled",
            "metric_keys": ["market_share"],
            "rule_summary": "取最新年度市场份额，降序排名赋分。",
        },
        *[_economic_dimension(key, label, metric_key) for key, label, metric_key in ECONOMIC_DIMENSIONS],
        {
            "key": "three_efficiency",
            "label": "产品三效",
            "enabled": True,
            "weight": 10.0,
            "aggregation": "average_scaled",
            "metric_keys": ["three_eff"],
            "rule_summary": "劳产、人效、元效分别赋分，平均后再次排名。",
        },
        {
            "key": "self_research",
            "label": "自研能力",
            "enabled": True,
            "weight": 10.0,
            "aggregation": "average_scaled",
            "metric_keys": ["self_research"],
            "rule_summary": "自研收入占比与研发投入产出比赋分后取平均，再次排名。",
        },
        {
            "key": "ability",
            "label": "能力评测",
            "enabled": True,
            "weight": 10.0,
            "aggregation": "average_scaled",
            "metric_keys": ["ability"],
            "rule_summary": "原始评测分降序排名赋分；当前允许占位数据。",
        },
        {
            "key": "quality",
            "label": "品质服务",
            "enabled": True,
            "weight": 10.0,
            "aggregation": "average_scaled",
            "metric_keys": ["quality"],
            "rule_summary": "产品推广满意度降序排名赋分。",
        },
    ],
}


def default_model_definition() -> dict:
    return deepcopy(DEFAULT_MODEL_DEFINITION)


def split_legacy_economic_definition(definition: dict) -> tuple[dict, bool]:
    """Expand the former aggregate economic dimension without changing total weight."""
    migrated = deepcopy(definition)
    dimensions = migrated.get("dimensions")
    if not isinstance(dimensions, list):
        return migrated, False
    for index, item in enumerate(dimensions):
        metric_keys = item.get("metric_keys") or []
        if item.get("key") != "economic" or len(metric_keys) <= 1:
            continue
        available = [entry for entry in ECONOMIC_DIMENSIONS if entry[2] in metric_keys]
        if not available:
            return migrated, False
        split_weight = float(item.get("weight", 0)) / len(available)
        replacement = []
        for key, label, metric_key in available:
            dimension = _economic_dimension(key, label, metric_key, split_weight, item.get("enabled", True))
            dimension["options"] = deepcopy(item.get("options") or {"growth_absolute": True})
            replacement.append(dimension)
        migrated["dimensions"] = [*dimensions[:index], *replacement, *dimensions[index + 1:]]
        return migrated, True
    return migrated, False
