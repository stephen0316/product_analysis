"""Resolve rule-owned input years independently of the batch's label/year."""
import re
import unicodedata
from copy import deepcopy


def summary_year(summary: str) -> int | None:
    years = {int(value) if len(value) == 4 else 2000 + int(value)
             for value in re.findall(r"(?<!\d)((?:19|20)\d{2}|\d{2})\s*年", unicodedata.normalize("NFKC", summary))}
    if not years:
        return None
    if len(years) > 2 or (len(years) == 2 and max(years) - min(years) != 1):
        raise ValueError("规则中的取数年份不明确：同比仅支持指定年度与其上一年度，请确认年份")
    anchor = max(years)
    if not 2020 <= anchor <= 2100:
        raise ValueError("规则指定年份超出支持范围2020—2100")
    return anchor


def dimension_year(dimension: dict, batch_year: int) -> int:
    summary = dimension.get("rule_summary") or ""
    stated = summary_year(summary)
    options = dimension.get("options") or {}
    if (options.get("rule_parser") or {}).get("source_summary") == summary:
        plan = (options.get("parsed_rule") or {}).get("execution_plan")
        if plan:
            from .semantic_rules import read_plan, specified_year
            planned = specified_year(read_plan(plan))
            if stated is not None and planned is not None and stated != planned:
                raise ValueError("摘要与识别的取数年份不一致，请重新识别")
            return planned if planned is not None else (stated if stated is not None else batch_year)
    return stated if stated is not None else batch_year


def relative_wording(summary: str, year: int) -> str:
    """Normalize numeric years for recognition only; keep the original rule."""
    def replace(match):
        actual = int(match[1]) if len(match[1]) == 4 else 2000 + int(match[1])
        return "当年" if actual == year else "上年"
    text = re.sub(r"(?<!\d)((?:19|20)\d{2}|\d{2})\s*年", replace, unicodedata.normalize("NFKC", summary))
    text = re.sub(r"取当年\s*的?", "", text, count=1)
    return text


def concrete_defaults(definition: dict, year: int) -> dict:
    """Date only exact built-in presets; never rewrite user-authored wording."""
    from .model_defaults import default_model_definition
    from .constants import ECONOMIC_FIELDS, FIELD_LABELS
    from .cost_scope import LEGACY_SELF_RESEARCH_SUMMARY
    presets = {d["calculation_type"]: d["rule_summary"] for d in default_model_definition()["dimensions"]}
    result = deepcopy(definition)
    for item in result.get("dimensions", []):
        types = item.get("metric_keys") or []
        source = item.get("calculation_type") or (types[0] if len(types) == 1 else None)
        old = item.get("rule_summary", "")
        if source not in presets or (old != presets[source] and not (source == "self_research" and old == LEGACY_SELF_RESEARCH_SUMMARY)):
            continue
        if source in ECONOMIC_FIELDS:
            name = FIELD_LABELS[source]
            summary = (f"取{year}年的{name}与增幅，分别排名赋分，取平均后再次排名赋分；"
                       f"增幅按（{year}年{name}－{year-1}年{name}）÷ABS（{year-1}年{name}）计算，结果保留正负号。")
        elif source == "market_share":
            summary = presets[source].replace("最新年度", f"{year}年")
        else:
            summary = f"取{year}年的" + presets[source]
        item["rule_summary"] = summary
        options = item.get("options") or {}
        parser = options.get("rule_parser") or {}
        if parser.get("source_summary") == old:
            parser["source_summary"] = summary
    return result
