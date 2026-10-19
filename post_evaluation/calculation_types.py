"""Executable contracts for model indicators (never evaluate user formulas)."""
from __future__ import annotations

from copy import deepcopy
import math
import re
import unicodedata
from typing import Any

from .constants import ECONOMIC_FIELDS, FIELD_LABELS, PRODUCTS, expected_fields_for_metrics, field_label
from .model_defaults import default_model_definition
from .natural_rules import average_formula, economic_intent, growth_formula, linear_score_formula, ranking_intent, unsupported_claim
from .semantic_rules import compile_plan, read_plan
from .input_names import single_input_name


_DEFAULTS = {
    item["metric_keys"][0]: item for item in default_model_definition()["dimensions"]
}
_PIPELINES = {
    "single": ("select_data", "rank", "assign_score"),
    "economic": ("select_data", "calculate_growth", "rank", "assign_score", "average", "rerank", "assign_score"),
    "three_eff": ("select_data", "rank", "assign_score", "average", "rerank", "assign_score"),
    "self_research": ("select_data", "cap", "calculate_ratio", "rank", "assign_score", "average", "rerank", "assign_score"),
    "ability": ("placeholder", "rank", "assign_score"),
}
_STEP_FORMULAS = {
    # Parser formulas are assertions, not executable code. Only documented
    # representations of the fixed pipeline can be accepted.
    "select_data": {"value", "latest_year(market_share)", "current_year", "previous_year"},
    "rank": {"rank_desc(value)", "rank_asc(value)"},
    "assign_score": {"first_score-rank_decrement*(rank-1)", "10-0.5×(rank-1)", "10-0.5*(rank-1)"},
    "average": {"average(scores)", "mean(scores)"},
    "rerank": {"rank_desc(average)", "rank_desc(average_score)"},
    "cap": {"min(value,cap_percent/100)", "min(value,1)"},
    "calculate_growth": {"(current-previous)/abs(previous)", "(current-previous)/previous"},
    "calculate_ratio": {"research_revenue/research_cost_total"},
    "placeholder": {""},
}


def family(type_id: str) -> str:
    if type_id in ECONOMIC_FIELDS:
        return "economic"
    return type_id if type_id in {"three_eff", "self_research", "ability"} else "single"


def calculation_types() -> list[dict]:
    return [{
        "id": key, "label": item["label"] + ("（年度模拟）" if key == "ability" else ""),
        "algorithm": family(key), "rule_summary": item["rule_summary"],
        "inputs": [field_label(field).replace("2024年", "上一年度").replace("2025年", "评估年度") for field in raw_fields(key, 2025)],
        "parameters": ["ranking_direction", "first_score", "rank_decrement"]
        + (["growth_denominator_uses_absolute_value"] if key in ECONOMIC_FIELDS else [])
        + (["cap_percent"] if key == "self_research" else []),
        "output": "原始赋分、加权贡献及逐步来源追溯",
        "steps": list(_PIPELINES[family(key)]),
    } for key, item in _DEFAULTS.items()]


def raw_fields(type_id: str, year: int) -> list[str]:
    # Ability remains an explicitly labelled demo until PE-10 connects its source.
    return [] if type_id == "ability" else expected_fields_for_metrics([type_id], year)


def bindings(dimension: dict) -> list[tuple[str, str]]:
    """Legacy metric IDs infer a known type; new instances must name one explicitly."""
    metrics = dimension.get("metric_keys")
    if not isinstance(metrics, list) or not metrics or not all(isinstance(m, str) and m.strip() for m in metrics):
        raise ValueError("缺少非空计算标识")
    type_id = dimension.get("calculation_type")
    if type_id is not None:
        if not isinstance(type_id, str) or type_id not in _DEFAULTS:
            raise ValueError("指标的计算规则尚不支持，请确认原始数据及计分方式")
        if len(metrics) != 1:
            raise ValueError("一个计算类型只能绑定一个输出标识")
        return [(metrics[0], type_id)]
    if any(metric not in _DEFAULTS for metric in metrics):
        raise ValueError("包含未知计算标识；请补充原始数据名称和计分方式，让系统识别计算规则")
    return [(metric, metric) for metric in metrics]


def source_metrics(dimension: dict) -> list[str]:
    return list(dict.fromkeys(type_id for _, type_id in bindings(dimension)))


def finite_number(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _text(value: str) -> str:
    return re.sub(r"[\s，,。.;；]", "", unicodedata.normalize("NFKC", value))


def _formula_text(value: str) -> str:
    value = re.sub(r"\s", "", unicodedata.normalize("NFKC", value)).lower().replace("×", "*")
    for label in ("排名", "名次"):
        value = value.replace(label, "rank")
    return value.removeprefix("score=").removeprefix("得分=")


def local_rule(type_id: str, summary: str) -> dict | None:
    """Recognise business intent locally; wording is not an execution contract."""
    if type_id not in _DEFAULTS or not isinstance(summary, str):
        return None
    canonical = _DEFAULTS[type_id]["rule_summary"]
    direction = "ascending" if "升序" in summary else "descending"
    candidate = re.sub(r"(排名|排序)(?:并且|并|然后|再)(赋分|给分)", r"\1\2", summary).replace("升序", "降序")
    denominator_absolute = True
    if type_id in ECONOMIC_FIELDS:
        candidate = unicodedata.normalize("NFKC", candidate)
        if "÷(上年值)" in candidate and "÷ABS(上年值)" not in candidate:
            denominator_absolute = False
            candidate = candidate.replace("÷(上年值)", "÷ABS(上年值)")
    # Composite rules rank component values in the chosen direction, then rank
    # their average scores descending (higher assigned score always means better).
    if family(type_id) != "single" and "降序" not in canonical:
        candidate = re.sub(r"[，,；;]?按?(?:数值)?降序排名[，,；;]?", "", candidate)
    intent = ranking_intent(type_id, summary)
    explicit_name = single_input_name(summary) if family(type_id) == "single" else None
    if intent is None and explicit_name and not re.search(r"(?:19|20)\d{2}|\d{2}年", explicit_name) and type_id in {"quality", "market_share"}:
        # Only the simple raw-value ranking grammar is reused. Source identity
        # is resolved by the existing label/hint ambiguity check, not this name.
        from .natural_rules import SUBJECTS
        known_other = any(explicit_name in names for key, names in SUBJECTS.items() if key != type_id)
        if not known_other:
            intent = ranking_intent(type_id, summary.replace(explicit_name, SUBJECTS[type_id][0], 1))
    economic = economic_intent(summary, FIELD_LABELS[type_id]) if type_id in ECONOMIC_FIELDS else None
    if type_id in ECONOMIC_FIELDS and economic is None:
        return None
    if _text(candidate) != _text(canonical) and intent is None and economic is None:
        return None
    if intent:
        direction = intent["ranking_direction"]
    if economic:
        direction = economic["ranking_direction"]
        denominator_absolute = economic["growth_denominator_uses_absolute_value"]
    params = {"first_score": None, "rank_decrement": None, "cap_percent": None,
              "growth_denominator_uses_absolute_value": denominator_absolute if type_id in ECONOMIC_FIELDS else None}
    if intent:
        params.update(intent["parameters"])
    return {
        "normalized_summary": summary, "ranking_direction": direction,
        "time_window": "latest_two_years" if type_id in ECONOMIC_FIELDS else "latest_year",
        "tie_method": "competition",
        "required_inputs": [{"name": explicit_name or field_label(field), "concept": field_label(field)[5:],
                             "period": "previous_year" if field.endswith(":2024") else "current_year", "required": True}
                            for field in raw_fields(type_id, 2025)],
        "steps": [{"order": index, "operation": operation, "description": operation, "formula": ""}
                  for index, operation in enumerate(_PIPELINES[family(type_id)], 1)],
        "parameters": params, "assumptions": [], "warnings": [], "confidence": 1,
    }


def _choose_binding(candidates: list[str], label: str, hint: str | None) -> str:
    if len(candidates) == 1:
        return candidates[0]
    named = [key for key in candidates if _text(label).replace("/", "／") == _text(_DEFAULTS[key]["label"]).replace("/", "／")]
    if len(named) == 1:
        return named[0]
    if hint in candidates:
        return hint
    raise ValueError("规则中省略了原始数据名称，请说明使用收入、毛利或其他哪项数据")


def local_rule_binding(label: str, summary: str, hint: str | None = None) -> tuple[str, dict] | None:
    """Resolve business wording to an internal source/algorithm, without a UI selector."""
    candidates = {key: rule for key in _DEFAULTS if (rule := local_rule(key, summary)) is not None}
    if not candidates:
        return None
    key = _choose_binding(list(candidates), label, hint)
    return key, candidates[key]


def parsed_rule_binding(label: str, summary: str, rule: dict, hint: str | None = None) -> str:
    if isinstance(rule, dict) and "execution_plan" in rule:
        plan = read_plan(rule["execution_plan"])
        source = plan.source_metric or ""
        compile_plan(rule["execution_plan"], source)
        return source
    candidates, errors = [], {}
    for key in _DEFAULTS:
        try:
            compile_rule({"rule_summary": summary, "options": {"parsed_rule": rule, "rule_parser": {"source_summary": summary}}}, key)
            candidates.append(key)
        except ValueError as exc:
            errors[key] = str(exc)
    if candidates:
        return _choose_binding(candidates, label, hint)
    named = next((key for key, item in _DEFAULTS.items() if _text(item["label"]) == _text(label)), None)
    raise ValueError(errors.get(hint or named, "当前规则的数据或计分方式尚不支持，请确认业务口径"))


def _validate_inputs(type_id: str, rule: dict, locally_understood: bool) -> None:
    inputs = rule.get("required_inputs")
    if inputs is None and locally_understood:
        return  # Earlier local models did not store this descriptive list.
    if not isinstance(inputs, list) or (not inputs and not locally_understood and type_id != "ability"):
        raise ValueError("尚未明确规则需要的原始数据，请补充说明")
    concepts = {field.split(":")[0] for field in raw_fields(type_id, 2025)}
    concepts.update(FIELD_LABELS.get(field, field) for field in list(concepts))
    aliases = {
        "market_share": {"行业份额", "市场占有率", "份额"},
        "quality": {"产品推广满意度", "客户满意度", "满意度", "质量服务"},
        "ability": {"原始评测分", "评测得分", "能力评测", "能力测评"},
        "three_eff": {"劳动生产率"},
        "self_research": {"自有占比", "研发总成本", "研发成本", "研发成本合计", "自研收入", "自研成本"},
    }
    concepts.update(aliases.get(type_id, set()))
    if type_id in ECONOMIC_FIELDS:
        concepts.update({"原值", "值", "产品" + FIELD_LABELS[type_id]})
    for item in inputs:
        if not isinstance(item, dict):
            raise ValueError("识别的数据需求格式无效，请重新检查")
        if not item.get("required", True):
            continue
        concept = re.sub(r"^(?:最新年度|评估年度|本年度|当年|上年|上一年度|\d{4}年)", "", str(item.get("concept") or item.get("name") or ""))
        if concept in {"产品线", "产品", "公司产品"}:
            continue
        if concept not in concepts:
            raise ValueError(f"摘要需要“{item.get('name') or concept}”，与所选指标的原始数据不一致，请确认数据含义")
        if item.get("period") not in {"current_year", "latest_year", "previous_year"}:
            raise ValueError(f"请明确“{item.get('name') or concept}”使用哪个年度")
        if item.get("period") == "previous_year" and type_id not in ECONOMIC_FIELDS:
            raise ValueError("当前指标使用评估年度原值，摘要还要求上一年度数据，请确认评估口径")


def compile_rule(dimension: dict, type_id: str, ranking: dict | None = None, *, executable: bool = True) -> dict:
    """Compile supported parameters only, rejecting claims we cannot execute."""
    if ranking is not None and not isinstance(ranking, dict):
        raise ValueError("赋分参数格式无效")
    options = dimension.get("options", {})
    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise ValueError("规则参数格式无效")
    if set(options) - {"growth_denominator_absolute", "growth_absolute", "parsed_rule", "rule_parser"}:
        raise ValueError("包含未支持的规则选项")
    for flag in ("growth_denominator_absolute",):
        if flag in options and not isinstance(options[flag], bool):
            raise ValueError("增幅分母选项必须为是或否")
    if "growth_absolute" in options:
        raise ValueError("不支持增幅整体取绝对值，请使用绝对值分母并保留正负号")
    summary = dimension.get("rule_summary", "")
    if not isinstance(summary, str):
        raise ValueError("规则摘要必须为文字")
    semantic = options.get("parsed_rule")
    if isinstance(semantic, dict) and "execution_plan" in semantic:
        parser = options.get("rule_parser")
        if not isinstance(parser, dict) or parser.get("source_summary") != summary:
            raise ValueError("规则摘要已变更，请在离开输入框后重新识别")
        return compile_plan(semantic["execution_plan"], type_id, ranking)
    if unsupported_claim(summary):
        raise ValueError("当前支持按名次赋分，尚不支持直接满分或额外加分")
    local = local_rule(type_id, summary)
    parsed = options.get("parsed_rule")
    if parsed is not None:
        if not isinstance(parsed, dict):
            raise ValueError("已识别规则格式无效")
        parser = options.get("rule_parser") or {}
        if not isinstance(parser, dict):
            raise ValueError("规则识别记录格式无效，请重新检查")
        if parser.get("source_summary") != summary:
            if executable:
                raise ValueError("规则摘要已变更，请重新检查后发布")
        if local and parser.get("provider") == "gemini" and not parser.get("execution_contract"):
            # Old AI records contain explanatory steps, omitted implicit scoring
            # and ordinary caveats, not the engine's execution contract. A clear
            # business statement can be reinterpreted locally without forcing the
            # user to rewrite it. An explicit local growth formula is authoritative
            # over a legacy AI boolean; retain malformed values for validation.
            parameters = deepcopy(parsed.get("parameters", {}))
            if isinstance(parameters, dict) and type_id in ECONOMIC_FIELDS:
                absolute = parameters.get("growth_denominator_uses_absolute_value")
                if absolute is None or isinstance(absolute, bool):
                    parameters["growth_denominator_uses_absolute_value"] = local["parameters"]["growth_denominator_uses_absolute_value"]
            parsed = {**local, "parameters": parameters}
        if parsed.get("warnings"):
            raise ValueError("有业务口径待确认：" + "；".join(map(str, parsed["warnings"])))
        if parsed.get("tie_method") not in {"competition", "not_specified"}:
            raise ValueError("仅支持竞争排名，不支持当前并列处理方式")
        if parsed.get("ranking_direction") not in {"ascending", "descending"}:
            raise ValueError("排名方向尚未明确")
        if parsed.get("time_window") not in {"latest_year", "latest_two_years", "not_specified"}:
            raise ValueError("不支持自定义时间窗口")
        if type_id not in ECONOMIC_FIELDS and parsed.get("time_window") == "latest_two_years":
            raise ValueError("当前计算类型只读取评估年度，不支持两年度窗口")
        steps = parsed.get("steps")
        allowed = set(_PIPELINES[family(type_id)])
        if not isinstance(steps, list) or not steps:
            raise ValueError("规则缺少可执行步骤")
        operations = []
        growth_absolute = None
        for index, step in enumerate(steps, 1):
            if not isinstance(step, dict) or step.get("operation") not in allowed:
                description = str(step.get("description") or "该计分方式") if isinstance(step, dict) else "该计分方式"
                raise ValueError(f"当前尚不支持第 {index} 步：{description}")
            if step.get("order") != index:
                raise ValueError("计算步骤顺序无效")
            formula = step.get("formula", "")
            # A data-selection formula is descriptive notation from the parser,
            # not arithmetic. The source contract owns which field is read.
            if not isinstance(formula, str):
                raise ValueError(f"第 {index} 步的说明格式无效")
            formula_valid = True
            if formula and step["operation"] == "assign_score":
                claimed = parsed.get("parameters") if isinstance(parsed.get("parameters"), dict) else {}
                first = claimed.get("first_score") if claimed.get("first_score") is not None else (ranking or {}).get("first_score", 10)
                decrement = claimed.get("rank_decrement") if claimed.get("rank_decrement") is not None else (ranking or {}).get("decrement", .5)
                # Invalid parameters are reported below, independently of notation.
                formula_valid = not (finite_number(first) and finite_number(decrement)) or linear_score_formula(formula, first, decrement)
            elif formula and step["operation"] == "calculate_growth":
                absolute = growth_formula(formula, FIELD_LABELS.get(type_id, ""))
                formula_valid = absolute is not None
                if absolute is not None:
                    if growth_absolute is not None and growth_absolute != absolute:
                        raise ValueError("不同步骤的增幅分母口径不一致，请确认是否取绝对值")
                    growth_absolute = absolute
            elif formula and step["operation"] == "average":
                if type_id in ECONOMIC_FIELDS:
                    subject = FIELD_LABELS[type_id]
                    components = (("current_score", "value_score", "当年值赋分", "当年值得分", f"当年{subject}得分"),
                                  ("growth_score", "growth_rate_score", "增幅赋分", "增幅得分", f"{subject}增幅得分"))
                elif type_id == "three_eff":
                    components = (("劳产得分", "labor_score"), ("人效得分", "human_score"), ("元效得分", "yuan_score"))
                else:
                    components = (("自研占比得分", "share_score"), ("ROI得分", "roi_score"))
                formula_valid = average_formula(formula, components)
            elif formula and step["operation"] not in {"select_data", "rank", "placeholder"}:
                formula_valid = _formula_text(formula) in {_formula_text(item) for item in _STEP_FORMULAS[step["operation"]]}
            if not formula_valid:
                raise ValueError(f"第 {index} 步的计分方式尚不支持，请明确其业务含义")
            if step["operation"] == "rank" and formula and (("rank_asc" in formula and parsed["ranking_direction"] != "ascending") or ("rank_desc" in formula and parsed["ranking_direction"] != "descending")):
                raise ValueError("排名公式与排名方向不一致")
            operations.append(step["operation"])
        essential = set(_PIPELINES[family(type_id)])
        # Raw input declarations already describe data selection. The AI may
        # begin its explanation at growth/ranking without a separate select step.
        if parsed.get("required_inputs") and family(type_id) != "ability":
            essential.discard("select_data")
        starts = {"select_data", "placeholder", "calculate_growth", "rank", "cap", "calculate_ratio"}
        if not essential.issubset(operations) or operations[-1] != "assign_score" or operations[0] not in starts:
            raise ValueError("识别的计分步骤不完整或与当前指标不一致，请补充说明")
        if "average" in operations and (operations.index("average") > operations.index("rerank") or any(operations.index(op) > operations.index("average") for op in ("rank", "calculate_growth", "calculate_ratio") if op in operations)):
            raise ValueError("平均、排名与原值计算的先后关系不一致，请明确计算顺序")
        if local and parsed["ranking_direction"] != local["ranking_direction"]:
            raise ValueError("摘要与已识别的排名方向不一致，请重新检查")
        if unsupported_claim(str(parsed.get("normalized_summary", ""))):
            raise ValueError("识别的说明包含未支持的直接满分或额外加分，请确认业务口径")
        _validate_inputs(type_id, parsed, local is not None)
    elif executable and local is None:
        raise ValueError("尚未明确这条规则的计算含义，请说明使用什么数据、如何排名或计分")

    rule = parsed or local or {}
    parameters = rule.get("parameters", {})
    if parameters is None:
        parameters = {}
    if not isinstance(parameters, dict):
        raise ValueError("计算参数格式无效")
    if parsed is not None and growth_absolute is not None:
        stated = parameters.get("growth_denominator_uses_absolute_value")
        if stated is not None and stated != growth_absolute:
            raise ValueError("增幅公式与分母绝对值设置不一致，请确认业务口径")
        parameters = {**parameters, "growth_denominator_uses_absolute_value": growth_absolute}
    supported = {"first_score", "rank_decrement", "cap_percent", "growth_denominator_uses_absolute_value"}
    if set(parameters) - supported:
        raise ValueError("包含未支持的计算参数")
    settings = {**(ranking or {}), "direction": rule.get("ranking_direction", "descending")}
    for parameter, key, positive in (("first_score", "first_score", True), ("rank_decrement", "decrement", False)):
        value = parameters.get(parameter)
        stated = (local or {}).get("parameters", {}).get(parameter)
        if stated is not None:
            if value is not None and value != stated:
                raise ValueError("摘要与识别的赋分参数不一致，请重新检查首名分或递减值")
            value = stated
        if value is not None:
            if not finite_number(value) or (value <= 0 if positive else value < 0):
                raise ValueError("首名分必须大于0、递减值必须非负，且均须为有限数值")
            settings[key] = value
    cap = parameters.get("cap_percent")
    if cap is not None and (type_id != "self_research" or not finite_number(cap) or not 0 < cap <= 100):
        raise ValueError("占比封顶仅支持自研收入占比，范围为大于0且不超过100%")
    absolute = parameters.get("growth_denominator_uses_absolute_value")
    if absolute is not None and (type_id not in ECONOMIC_FIELDS or not isinstance(absolute, bool)):
        raise ValueError("绝对值分母参数仅适用于经济效益，且必须为是或否")
    if type_id in ECONOMIC_FIELDS and local:
        expected_absolute = local["parameters"]["growth_denominator_uses_absolute_value"]
        if parsed is None and options.get("growth_denominator_absolute") is False and expected_absolute:
            raise ValueError("摘要与增幅分母参数不一致，请明确是否取分母绝对值")
        actual_absolute = absolute if absolute is not None else options.get("growth_denominator_absolute", True)
        if expected_absolute != actual_absolute:
            raise ValueError("摘要与增幅分母参数不一致，请明确是否取分母绝对值")
    first, decrement = settings.get("first_score", 10), settings.get("decrement", .5)
    if (not finite_number(first) or first <= 0 or not finite_number(decrement) or decrement < 0
            or not math.isfinite((float(first) - (len(PRODUCTS) - 1) * float(decrement)) / float(first))):
        raise ValueError("赋分参数超出可计算范围，请检查首名分和递减值")
    return {
        "ranking": settings, "final_ranking": {**settings, "direction": "descending"},
        "cap": (cap if cap is not None else 100) / 100,
        "growth_denominator_absolute": absolute if absolute is not None else options.get("growth_denominator_absolute", True),
        "rule": deepcopy(rule),
    }


def rule_readiness(dimension: dict, ranking: dict | None = None) -> dict:
    try:
        bound = bindings(dimension)
        compiled = [compile_rule(dimension, type_id, ranking) for _, type_id in bound]
        return {"key": dimension.get("key"), "executable": True, "errors": [],
                "calculation_type": bound[0][1], "normalized_summary": compiled[0]["rule"].get("normalized_summary", "")}
    except ValueError as exc:
        return {"key": dimension.get("key"), "executable": False, "errors": [str(exc)]}
