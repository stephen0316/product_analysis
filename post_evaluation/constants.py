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
}

ECONOMIC_FIELDS = [
    "revenue", "gross_profit", "gross_margin", "operating_profit", "operating_margin"
]
THREE_EFF_FIELDS = ["labor_output", "yuan_efficiency", "human_efficiency"]


def expected_fields(role: str, year: int) -> list[str]:
    if role == "market":
        return [f"market_share:{year}"]
    if role == "finance":
        return [f"{metric}:{period}" for metric in ECONOMIC_FIELDS for period in (year - 1, year)]
    if role == "three_eff":
        return [f"{metric}:{year}" for metric in THREE_EFF_FIELDS]
    raise ValueError("未知数据类型")


def field_label(key: str) -> str:
    metric, year = key.split(":", 1)
    return f"{year}年{FIELD_LABELS.get(metric, metric)}"
