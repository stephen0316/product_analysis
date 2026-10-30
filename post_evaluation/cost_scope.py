"""Explicit, bounded R&D denominator selection shared by every pipeline stage."""
from __future__ import annotations

import re
import unicodedata
from copy import deepcopy

from .constants import FIELD_LABELS, SELF_RESEARCH_COST_FIELDS

LEGACY_SELF_RESEARCH_SUMMARY = "自研收入占比与研发投入产出比赋分后取平均，再次排名。"
DEFAULT_SELF_RESEARCH_SUMMARY = (LEGACY_SELF_RESEARCH_SUMMARY +
    "研发投入产出比＝研发收入÷研发总成本；研发总成本取：" +
    "、".join(FIELD_LABELS[key] for key in SELF_RESEARCH_COST_FIELDS) + "，各项相加。")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def cost_scope(summary: str) -> tuple[list[str], str]:
    """Return selected known cost keys and text with explicit cost clauses removed.

    Legacy statements retain the original seven-field denominator. Unknown or
    exclusion-based lists fail closed; they must not silently restore defaults.
    """
    pattern = re.compile(r"(?:研发总成本|研发成本|成本范围|成本|分母)\s*(?:仅取|只取|取|采用|使用|包括|包含|为|由)|(?:仅取|只取|取)(?=\s*研究开发费)")
    clauses = []
    selected = None
    for match in pattern.finditer(summary):
        end = re.search(r"[。；;\n]", summary[match.end():])
        stop = match.end() + end.start() if end else len(summary)
        if any(start <= match.start() < finish for start, finish in clauses):
            continue
        text = _normalize(summary[match.end():stop]).lstrip(":：")
        if not text:
            raise ValueError("研发成本范围不能为空，请至少选择一项成本")
        # A generic total without a declared list is legacy wording, not a scope.
        if "研究开发费" not in text:
            raise ValueError("请明确研发成本范围，使用完整成本名称列出需计入的项目")
        remainder = text
        keys = []
        for key in SELF_RESEARCH_COST_FIELDS:
            name = _normalize(FIELD_LABELS[key])
            if name in remainder:
                keys.append(key)
                remainder = remainder.replace(name, "")
        remainder = re.sub(r"各项相加|相加|的合计|合计|之和|求和|汇总|相加之和", "", remainder)
        remainder = re.sub(r"[、,，+＋及和与:：]", "", remainder)
        if not keys or remainder:
            raise ValueError("研发成本范围含未知或有歧义的项目，请用完整成本名称明确列出，不支持自动猜测或排除项")
        if selected is not None:
            raise ValueError("研发成本范围被重复定义，请保留一份明确的成本清单")
        selected = keys
        clauses.append((match.start(), stop + (1 if end else 0)))
    base = summary
    for start, end in reversed(clauses):
        base = base[:start] + base[end:]
    # This formula describes the supported ratio, not an extra execution step.
    base = re.sub(r"研发投入产出比\s*[＝=]\s*研发收入\s*[÷/]\s*研发总成本[；;。]?", "", base)
    if selected is None and any(_normalize(FIELD_LABELS[k]) in _normalize(summary) for k in SELF_RESEARCH_COST_FIELDS):
        raise ValueError("请以“研发总成本取：成本项目”的形式明确成本范围")
    return list(SELF_RESEARCH_COST_FIELDS if selected is None else selected), base


def expand_legacy_cost_summary(definition: dict) -> dict:
    """Only editable drafts get an explicit version of the identical old scope."""
    result = deepcopy(definition)
    for item in result.get("dimensions", []):
        if (item.get("calculation_type") == "self_research" or "self_research" in item.get("metric_keys", [])) and item.get("rule_summary") == LEGACY_SELF_RESEARCH_SUMMARY:
            item["rule_summary"] = DEFAULT_SELF_RESEARCH_SUMMARY
            options = dict(item.get("options") or {})
            options.pop("parsed_rule", None)
            options.pop("rule_parser", None)
            item["options"] = options
    return result
