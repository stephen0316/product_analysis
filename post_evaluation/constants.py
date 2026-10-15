from __future__ import annotations

PRODUCTS = [
    "OnePark", "芯片", "通信模组", "物联卡", "OneSec", "OneNET", "交通物流",
    "商客标品", "商客场景", "5G行业产品", "千里眼", "和对讲", "云视讯",
]

METRICS = [
    ("market_share", "市场份额", "market"),
    ("revenue", "收入／增幅", "finance"),
    ("gross_profit", "毛利／增幅", "finance"),
    ("gross_margin", "毛利率／增幅", "finance"),
    ("operating_profit", "营业利润／增幅", "finance"),
    ("operating_margin", "营业利润率／增幅", "finance"),
    ("three_eff", "产品三效", "three_eff"),
    ("self_research", "自研占比／ROI", None),
    ("ability", "能力评测", None),
    ("quality", "品质服务", None),
]

METRIC_NAMES = {key: label for key, label, _ in METRICS}
ROLES = {
    "market": "市场份额",
    "finance": "经济效益",
    "three_eff": "产品三效",
    "self_research": "自研能力",
    "generic": "通用数据",
}

FIELD_LABELS = {
    "market_share": "市场份额",
    "revenue": "收入",
    "gross_profit": "毛利",
    "gross_margin": "毛利率",
    "operating_profit": "营业利润额",
    "operating_margin": "营业利润率",
    "labor_output": "劳产",
    "yuan_efficiency": "元效",
    "human_efficiency": "人效",
    "self_research": "自研能力",
    "self_owned_ratio": "自研收入占比",
    "research_revenue": "研发收入",
    "research_cost_own_labor": "研究开发费-自有人工-项目成本",
    "research_cost_partner_labor": "研究开发费-合作人工",
    "research_cost_travel": "研究开发费-差旅费",
    "research_cost_external_independent": "研究开发费-其他外采（自立）",
    "research_cost_external_internal": "研究开发费-其他外采（委内）",
    "research_cost_resource_pool": "研究开发费-资源池成本",
    "research_cost_allocation": "研究开发费-其他分摊-项目成本",
    "ability": "能力评测",
    "quality": "品质服务",
}

ECONOMIC_FIELDS = [
    "revenue", "gross_profit", "gross_margin", "operating_profit", "operating_margin"
]
THREE_EFF_FIELDS = ["labor_output", "yuan_efficiency", "human_efficiency"]
SELF_RESEARCH_COST_FIELDS = [
    "research_cost_own_labor",
    "research_cost_partner_labor",
    "research_cost_travel",
    "research_cost_external_independent",
    "research_cost_external_internal",
    "research_cost_resource_pool",
    "research_cost_allocation",
]
SELF_RESEARCH_FIELDS = ["self_owned_ratio", "research_revenue", *SELF_RESEARCH_COST_FIELDS]


def expected_fields(role: str, year: int) -> list[str]:
    if role == "market":
        return [f"market_share:{year}"]
    if role == "finance":
        return [f"{metric}:{period}" for metric in ECONOMIC_FIELDS for period in (year - 1, year)]
    if role == "three_eff":
        return [f"{metric}:{year}" for metric in THREE_EFF_FIELDS]
    raise ValueError("未知数据类型")


def expected_fields_for_metrics(metric_keys: list[str], year: int) -> list[str]:
    """Return the raw fields a user-selected scoring dimension needs.

    The upload contract is keyed to scoring dimensions, not legacy workbook
    categories.  A few legacy dimensions expand into their raw source fields.
    """
    fields: list[str] = []
    for metric in metric_keys:
        if metric == "market_share":
            fields.append(f"market_share:{year}")
        elif metric in ECONOMIC_FIELDS:
            fields.extend((f"{metric}:{year - 1}", f"{metric}:{year}"))
        elif metric == "three_eff":
            fields.extend(f"{field}:{year}" for field in THREE_EFF_FIELDS)
        elif metric == "self_research":
            fields.extend(f"{field}:{year}" for field in SELF_RESEARCH_FIELDS)
        elif metric in {"ability", "quality"}:
            fields.append(f"{metric}:{year}")
    return list(dict.fromkeys(fields))


def display_fields_for_metrics(metric_keys: list[str], year: int) -> list[str]:
    """Return optional display-only fields.

    Historical market-share values are intentionally excluded: they do not
    participate in ranking and the exported review workbook should contain
    only model-owned scoring data.
    """
    return []


def mapping_fields_for_metrics(metric_keys: list[str], year: int) -> list[str]:
    return list(dict.fromkeys([
        *expected_fields_for_metrics(metric_keys, year),
        *display_fields_for_metrics(metric_keys, year),
    ]))


def adapter_for_metrics(metric_keys: list[str]) -> str:
    """Choose a legacy reader only when the selected dimensions share one layout."""
    keys = set(metric_keys)
    if keys == {"market_share"}:
        return "market"
    if keys and keys.issubset(set(ECONOMIC_FIELDS)):
        return "finance"
    if keys == {"three_eff"}:
        return "three_eff"
    if keys == {"self_research"}:
        return "self_research"
    return "generic"


def input_specs_for_dimension(dimension: dict, year: int) -> list[dict]:
    """Build the raw-input contract consumed by upload matching.

    The supported calculation type owns the raw fields. New indicator instances
    reuse that contract; AI-extracted text cannot create an unimplemented input.
    """
    from .calculation_types import raw_fields, source_metrics
    fields = list(dict.fromkeys(field for type_id in source_metrics(dimension) for field in raw_fields(type_id, year)))
    return [{"key": key, "label": field_label(key), "required": True} for key in fields]


def display_specs_for_dimension(dimension: dict, year: int) -> list[dict]:
    return [
        {"key": key, "label": field_label(key), "required": False}
        for key in display_fields_for_metrics(dimension.get("metric_keys") or [], year)
    ]


def calculation_contracts(dimensions: list[dict], targets: list[str], year: int) -> list[dict]:
    """Return business-facing formulas and their raw-input slots."""
    selected = set(targets)
    contracts = []
    for dimension in dimensions:
        if dimension.get("key") not in selected:
            continue
        specs = input_specs_for_dimension(dimension, year)
        display_specs = display_specs_for_dimension(dimension, year)
        inputs = [{"key": item["key"], "label": item["label"], "required": True} for item in specs]
        display_inputs = [{"key": item["key"], "label": item["label"], "required": False} for item in display_specs]
        metric_keys = dimension.get("metric_keys") or []
        if metric_keys == ["self_research"]:
            input_groups = [
                {"label": f"{year}年自研收入占比", "keys": [f"self_owned_ratio:{year}"]},
                {"label": f"{year}年研发收入", "keys": [f"research_revenue:{year}"]},
                {"label": f"{year}年研发成本（E～K项合计）", "keys": [f"{field}:{year}" for field in SELF_RESEARCH_COST_FIELDS]},
            ]
            formula = "自研收入占比与研发投入产出比分别排名赋分；研发投入产出比＝研发收入÷研发成本合计；两项得分取平均后再次排名赋分。"
        else:
            input_groups = [{"label": item["label"], "keys": [item["key"]]} for item in specs]
            formula = str(dimension.get("rule_summary") or "按模型规则计算。")
        contracts.append({
            "dimension_key": dimension.get("key"),
            "dimension_label": dimension.get("label"),
            "rule_summary": dimension.get("rule_summary", ""),
            "formula": formula,
            "inputs": inputs,
            "display_inputs": display_inputs,
            "input_groups": input_groups,
            "display_groups": [{"label": item["label"], "keys": [item["key"]]} for item in display_specs],
        })
    return contracts


def field_label(key: str) -> str:
    metric, year = key.split(":", 1)
    return f"{year}年{FIELD_LABELS.get(metric, metric)}"
