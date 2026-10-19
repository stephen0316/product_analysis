"""Business input names shared by rule interpretation and workbook matching."""
from __future__ import annotations

import re
import unicodedata


def normalize_name(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()


def single_input_name(summary: str) -> str | None:
    """Extract only a complete, simple raw-value ranking clause, never prose/code."""
    text = unicodedata.normalize("NFKC", summary).strip().rstrip("。.")
    match = re.fullmatch(
        r"(?:取(?:最新年度|评估年度|当年)?|根据|按照|按)?\s*[“\"「]?"
        r"(.{1,100}?)\s*[”\"」]?\s*[,，]?\s*(?:按)?"
        r"(?:降序|升序|从高到低|从低到高|从大到小|从小到大)\s*"
        r"(?:排名|排序)\s*(?:并|后)?\s*(?:赋分|给分|计分|评分)", text,
    )
    if not match:
        return None
    name = match[1].strip().strip('“”"「」')
    # Compound rules or conditions need their structured interpretation instead.
    if re.search(r"[，,;；。:+*/=<>]|(?:分别|平均|增幅|然后|如果|大于|小于|取最大|额外|直接|与|和|及)|(?:加|减|乘|除)\s*[\d一二三四五六七八九十]", name):
        return None
    return name or None


def declared_input_names(dimension: dict, year: int) -> dict[str, str]:
    """Map accepted raw-input slots to their explicit business names.

    Names are matching/display metadata; they never introduce engine fields or
    change a formula. Stale parser records cannot rename current dependencies.
    """
    from .calculation_types import raw_fields, source_metrics
    from .constants import FIELD_LABELS

    fields = [field for source in source_metrics(dimension) for field in raw_fields(source, year)]
    names = {}
    options = dimension.get("options") or {}
    parsed = options.get("parsed_rule") or {}
    parser = options.get("rule_parser") or {}
    if parser.get("source_summary") == dimension.get("rule_summary"):
        plan = parsed.get("execution_plan") or {}
        for item in plan.get("inputs", []):
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                continue
            period = year - 1 if item.get("period") == "previous_year" else year
            key = f"{item.get('field')}:{period}"
            if key in fields and item["name"].strip() and normalize_name(bare_input_name(item["name"])) in normalize_name(str(dimension.get("rule_summary") or "")):
                names[key] = item["name"].strip()
        for item in parsed.get("required_inputs", []):
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                continue
            period = year - 1 if item.get("period") == "previous_year" else year
            candidates = [key for key in fields if key.endswith(f":{period}") and
                          item.get("concept") in {key.split(":")[0], FIELD_LABELS.get(key.split(":")[0])}]
            if len(candidates) == 1 and candidates[0] not in names:
                name = item["name"].strip()
                canonical = FIELD_LABELS.get(candidates[0].split(":")[0], "")
                if normalize_name(bare_input_name(name)) == normalize_name(canonical) or normalize_name(bare_input_name(name)) in normalize_name(str(dimension.get("rule_summary") or "")):
                    names[candidates[0]] = name
    if len(fields) == 1:
        explicit = single_input_name(str(dimension.get("rule_summary") or ""))
        if explicit:
            names[fields[0]] = explicit
    return names


def bare_input_name(value: str) -> str:
    """Remove a declaration's period prefix for year-aware header matching."""
    value = re.sub(r"^(?:上一年度|上一年|上年|去年|评估年度|最新年度|本年度|当年|本年)", "", value.strip())
    return re.sub(r"^(?:(?:19|20)\d{2}|\d{2})年\s*", "", value)
