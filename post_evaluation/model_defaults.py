from __future__ import annotations

from copy import deepcopy


DEFAULT_MODEL_ID = "model-2025-v1"


DIMENSION_CATEGORIES = (
    ("market_share", "市场份额"),
    ("benefit_contribution", "效益贡献"),
    ("product_efficiency", "产品三效"),
    ("self_research", "自研能力"),
    ("quality_service", "品质服务"),
)

DEFAULT_EXPORT_SORT_INDICATOR = {
    "market_share": "market",
    "benefit_contribution": "economic_revenue",
    "product_efficiency": "three_efficiency",
    "self_research": "self_research",
    "quality_service": "quality",
}

CATEGORY_BY_INDICATOR_KEY = {
    "market": "market_share",
    "economic": "benefit_contribution",
    "three_efficiency": "product_efficiency",
    "self_research": "self_research",
    "ability": "self_research",
    "quality": "quality_service",
}


def dimension_categories() -> list[dict]:
    return [
        {
            "key": key,
            "label": label,
            "export_sort_indicator_key": DEFAULT_EXPORT_SORT_INDICATOR.get(key),
        }
        for key, label in DIMENSION_CATEGORIES
    ]


ECONOMIC_DIMENSIONS = (
    ("economic_revenue", "收入／增幅", "revenue"),
    ("economic_gross_profit", "毛利／增幅", "gross_profit"),
    ("economic_gross_margin", "毛利率／增幅", "gross_margin"),
    ("economic_operating_profit", "营业利润／增幅", "operating_profit"),
    ("economic_operating_margin", "营业利润率／增幅", "operating_margin"),
)

ECONOMIC_RULE_SUMMARY = "当年值与增幅分别赋分，取平均后再次排名赋分；增幅按（当年值－上年值）÷ABS（上年值）计算，结果保留正负号。"
LEGACY_ECONOMIC_RULE_SUMMARY = "当年值与绝对增幅分别赋分，取平均后再次排名赋分。"


def _economic_dimension(key: str, label: str, metric_key: str, weight: float = 10.0, enabled: bool = True) -> dict:
    return {
        "key": key,
        "group": "economic",
        "category_key": "benefit_contribution",
        "label": label,
        "enabled": enabled,
        "weight": weight,
        "aggregation": "average_scaled",
        "metric_keys": [metric_key],
        "calculation_type": metric_key,
        "rule_summary": ECONOMIC_RULE_SUMMARY,
        "options": {"growth_denominator_absolute": True},
    }


DEFAULT_MODEL_DEFINITION = {
    "categories": dimension_categories(),
    "ranking": {
        "method": "competition",
        "first_score": 10.0,
        "decrement": 0.5,
    },
    "dimensions": [
        {
            "key": "market",
            "category_key": "market_share",
            "label": "市场份额",
            "enabled": True,
            "weight": 10.0,
            "aggregation": "average_scaled",
            "metric_keys": ["market_share"],
            "calculation_type": "market_share",
            "rule_summary": "取最新年度市场份额，降序排名赋分。",
        },
        *[_economic_dimension(key, label, metric_key) for key, label, metric_key in ECONOMIC_DIMENSIONS],
        {
            "key": "three_efficiency",
            "category_key": "product_efficiency",
            "label": "产品三效",
            "enabled": True,
            "weight": 10.0,
            "aggregation": "average_scaled",
            "metric_keys": ["three_eff"],
            "calculation_type": "three_eff",
            "rule_summary": "劳产、人效、元效分别赋分，平均后再次排名。",
        },
        {
            "key": "self_research",
            "category_key": "self_research",
            "label": "自研能力",
            "enabled": True,
            "weight": 10.0,
            "aggregation": "average_scaled",
            "metric_keys": ["self_research"],
            "calculation_type": "self_research",
            "rule_summary": "自研收入占比与研发投入产出比赋分后取平均，再次排名。",
        },
        {
            "key": "ability",
            "category_key": "self_research",
            "label": "能力评测",
            "enabled": True,
            "weight": 10.0,
            "aggregation": "average_scaled",
            "metric_keys": ["ability"],
            "calculation_type": "ability",
            "rule_summary": "原始评测分降序排名赋分；当前允许占位数据。",
        },
        {
            "key": "quality",
            "category_key": "quality_service",
            "label": "品质服务",
            "enabled": True,
            "weight": 10.0,
            "aggregation": "average_scaled",
            "metric_keys": ["quality"],
            "calculation_type": "quality",
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
            dimension["options"] = {"growth_denominator_absolute": True}
            replacement.append(dimension)
        migrated["dimensions"] = [*dimensions[:index], *replacement, *dimensions[index + 1:]]
        return migrated, True
    return migrated, False


def ensure_economic_growth_formula(definition: dict) -> tuple[dict, bool]:
    """Migrate the former whole-result ABS formula to an absolute denominator."""
    migrated = deepcopy(definition)
    changed = False
    economic_metric_keys = {entry[2] for entry in ECONOMIC_DIMENSIONS}
    for indicator in migrated.get("dimensions") or []:
        # Explicit type bindings belong to the executable contract. Historical
        # formula migration must not reset parameters of newly configured models.
        if indicator.get("calculation_type"):
            continue
        metric_keys = set(indicator.get("metric_keys") or [])
        if indicator.get("group") != "economic" and not metric_keys.intersection(economic_metric_keys):
            continue
        options = dict(indicator.get("options") or {})
        if "growth_absolute" in options:
            options.pop("growth_absolute")
            changed = True
        if options.get("growth_denominator_absolute") is not True:
            options["growth_denominator_absolute"] = True
            changed = True
        if indicator.get("rule_summary") == LEGACY_ECONOMIC_RULE_SUMMARY:
            indicator["rule_summary"] = ECONOMIC_RULE_SUMMARY
            options.pop("parsed_rule", None)
            options.pop("rule_parser", None)
            changed = True
        indicator["options"] = options
    return migrated, changed


def ensure_indicator_categories(definition: dict) -> tuple[dict, bool]:
    """Add the dimension layer to legacy models without overwriting user-managed dimensions."""
    migrated = deepcopy(definition)
    changed = False
    categories = migrated.get("categories")
    if not isinstance(categories, list) or not categories:
        categories = dimension_categories()
        migrated["categories"] = categories
        changed = True
    category_keys = {
        str(item.get("key") or "") for item in categories
        if isinstance(item, dict) and str(item.get("key") or "").strip()
    }
    fallback_key = next(iter(category_keys), "market_share")
    for indicator in migrated.get("dimensions") or []:
        if indicator.get("category_key") in category_keys:
            continue
        indicator_key = str(indicator.get("key") or "")
        group = str(indicator.get("group") or "")
        suggested = CATEGORY_BY_INDICATOR_KEY.get(
            indicator_key,
            "benefit_contribution" if group == "economic" or indicator_key.startswith("economic_") else "market_share",
        )
        indicator["category_key"] = suggested if suggested in category_keys else fallback_key
        changed = True
    return migrated, changed
