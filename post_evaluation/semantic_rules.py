"""Typed business plans: the engine never interprets AI prose or formula strings."""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .constants import ECONOMIC_FIELDS, METRIC_NAMES, PRODUCTS, expected_fields_for_metrics, field_label

CONTRACT = "semantic-plan-v1"


class PlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    field: str = Field(min_length=1, max_length=100)
    period: Literal["current_year", "previous_year"]
    year: int | None = Field(default=None, ge=2019, le=2100, description="业务明确限定的完整年份；相对年度为null。允许2020年评估使用2019年同比基数。")


class ExecutionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    version: Literal["semantic-plan-v1"]
    source_metric: str | None
    strategy: Literal["single", "economic", "three_eff", "self_research", "ability", "unsupported"]
    inputs: list[PlanInput] = Field(max_length=50)
    data_mode: Literal["source", "annual_demo"]
    ranking_direction: Literal["ascending", "descending", "unclear"] = Field(description="对子项原值排序：从低到高或越小越好是ascending，从高到低或越大越好是descending。")
    final_ranking_direction: Literal["ascending", "descending", "not_applicable", "unclear"] = Field(description="仅指复合指标平均得分后的第二轮排名。single/ability必须not_applicable；复合指标高分优先是descending，不随子项原值排序方向改变。")
    tie_method: Literal["competition", "dense", "ordinal", "unclear"]
    time_window: Literal["latest_year", "latest_two_years", "custom", "unclear"]
    aggregation: Literal["none", "equal_scores", "weighted_scores", "raw_values", "other"]
    growth_denominator: Literal["absolute_previous", "previous", "not_applicable", "other"] = Field(description="增幅分母：'上年/去年绝对值'、'ABS(上年值)'必须absolute_previous；previous只表示保留上年原值自身的正负号、不取绝对值。无增幅步骤为not_applicable。")
    growth_preserve_sign: bool | None
    cap_percent: float | None
    first_score: float | None
    rank_decrement: float | None
    unsupported_features: list[str] = Field(max_length=20)
    questions: list[str] = Field(max_length=20)


class Interpretation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    normalized_summary: str = Field(min_length=1, max_length=3000)
    execution_plan: ExecutionPlan
    confidence: float = Field(ge=0, le=1)


def response_schema() -> dict:
    schema = Interpretation.model_json_schema()
    definitions = schema.pop("$defs", {})
    def expand(value):
        if isinstance(value, dict):
            if "$ref" in value:
                return expand(deepcopy(definitions[value["$ref"].split("/")[-1]]))
            return {key: expand(item) for key, item in value.items() if key != "title"}
        return [expand(item) for item in value] if isinstance(value, list) else value
    result = expand(schema)
    # Protocol identity is server metadata, not a business fact for the model
    # to infer. Older Gemini models do not reliably honour JSON Schema const.
    plan_schema = result["properties"]["execution_plan"]
    plan_schema["properties"].pop("version")
    plan_schema["required"].remove("version")
    # A positive enum avoids double-negation mistakes around preserve_sign.
    plan_schema["properties"].pop("growth_preserve_sign")
    plan_schema["required"].remove("growth_preserve_sign")
    plan_schema["properties"]["growth_result"] = {
        "type": "string", "enum": ["signed_change", "absolute_change", "not_applicable"],
        "description": "增幅结果的业务含义。signed_change：保留增长/下降方向，允许负增长，例如'不把负增长变正增长'、'增减方向保留'。absolute_change：对整个增幅取绝对值，把负增长也变正数。只对分母取绝对值时结果仍是signed_change。无增幅步骤为not_applicable。",
    }
    plan_schema["required"].append("growth_result")
    # Temporal span is a consequence of raw-input dependencies, not a second
    # independent LLM judgement which can contradict those same inputs.
    plan_schema["properties"].pop("time_window")
    plan_schema["required"].remove("time_window")
    input_schema = plan_schema["properties"]["inputs"]["items"]
    input_schema["properties"]["year"] = {
        "type": ["integer", "null"],
        "description": "仅在原文明确指定年份时填写完整年份，例如25年收入为2025，24年基数为2024；当年/上年等相对表达填null。不得用电脑当前年份补造。",
    }
    input_schema["properties"]["period"]["description"] = "业务数据在本次规则中的角色；原值所在评估年度为current_year，同比基数为previous_year。明确写25年不改变同比仍需本年及上年两项输入。"
    input_schema["required"].append("year")
    # Bounded nested arrays and numeric/string matchers inflate Gemini's
    # constrained-decoding state space. Keep generation structural; the full
    # Pydantic contract above still validates every returned value locally.
    def generation_schema(node):
        if isinstance(node, list):
            return [generation_schema(item) for item in node]
        if not isinstance(node, dict):
            return node
        limits = {"minItems", "maxItems", "minLength", "maxLength", "minimum", "maximum",
                  "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "pattern", "format", "default"}
        simplified = {key: generation_schema(item) for key, item in node.items() if key not in limits}
        alternatives = simplified.get("anyOf")
        if alternatives and all(set(item) == {"type"} for item in alternatives):
            simplified["type"] = [item["type"] for item in alternatives]
            del simplified["anyOf"]
        return simplified
    return generation_schema(result)


def source_strategy(source: str) -> str:
    if source in ECONOMIC_FIELDS:
        return "economic"
    return source if source in {"three_eff", "self_research", "ability"} else "single"


def input_contract(source: str) -> list[dict]:
    fields = [] if source == "ability" else expected_fields_for_metrics([source], 2025)
    return [{"field": field.split(":")[0], "period": "previous_year" if field.endswith(":2024") else "current_year"} for field in fields]


def source_catalog() -> list[dict]:
    return [{"source_metric": source, "name": label, "strategy": source_strategy(source),
             "inputs": input_contract(source), "data_mode": "annual_demo" if source == "ability" else "source"}
            for source, label in METRIC_NAMES.items()]


def read_plan(value) -> ExecutionPlan:
    try:
        return ExecutionPlan.model_validate(value)
    except ValidationError as exc:
        location = ".".join(map(str, exc.errors()[0]["loc"]))
        raise ValueError(f"识别的计算方案格式无效（{location}），请重新识别") from exc


def specified_year(plan: ExecutionPlan) -> int | None:
    """Resolve explicit input years to one evaluation year, without using wall time."""
    years = {item.year + (1 if item.period == "previous_year" else 0) for item in plan.inputs if item.year is not None}
    if len(years) > 1:
        raise ValueError("规则中的明确年份不一致：同比需要评估年度及其上一年度，不能跳年或混用年度")
    year = next(iter(years), None)
    if year is not None and not 2020 <= year <= 2100:
        raise ValueError("规则限定的评估年度超出支持范围2020—2100")
    return year


def plan_value(plan: ExecutionPlan) -> dict:
    value = plan.model_dump()
    value["inputs"] = [item.model_dump(exclude_none=True) for item in plan.inputs]
    return value


def validate_plan(value, source: str) -> ExecutionPlan:
    plan = read_plan(value)
    if plan.questions:
        questions = "；".join(plan.questions)
        raise ValueError(questions if questions.startswith("请") else "请确认：" + questions)
    if plan.unsupported_features:
        raise ValueError("已理解规则，但当前尚不支持：" + "；".join(plan.unsupported_features))
    if plan.source_metric not in METRIC_NAMES:
        raise ValueError("尚未明确所需原始数据，请说明使用收入、市场份额或其他哪项数据")
    if plan.source_metric != source:
        raise ValueError("规则需要的数据与指标绑定来源不同，请重新识别并确认来源")
    strategy = source_strategy(source)
    if plan.strategy != strategy:
        raise ValueError("已识别的计分步骤与当前支持的计算方案不一致")
    if plan.data_mode != ("annual_demo" if source == "ability" else "source"):
        raise ValueError("真实能力评测数据尚未接入；年度模拟值必须明确标记为模拟")
    expected = {(item["field"], item["period"]) for item in input_contract(source)}
    actual = {(item.field, item.period) for item in plan.inputs}
    if actual != expected or len(actual) != len(plan.inputs):
        missing = expected - actual
        extra = actual - expected
        details = []
        if missing:
            details.append("缺少" + "、".join(("上一年度" if period == "previous_year" else "评估年度") + field_label(field + ":2025")[5:] for field, period in sorted(missing)))
        if extra:
            details.append("包含不支持的数据：" + "、".join(field for field, _ in sorted(extra)))
        raise ValueError("计算方案的数据需求不完整或不一致：" + ("；".join(details) or "同一数据重复声明"))
    specified_year(plan)
    if plan.ranking_direction == "unclear":
        raise ValueError("请说明原始数值越大越好，还是越小越好")
    if plan.tie_method != "competition":
        raise ValueError("当前仅支持并列同名次、后续名次跳位的竞争排名")
    if plan.time_window != ("latest_two_years" if strategy == "economic" else "latest_year"):
        raise ValueError("计算方案的年度范围与所需原始数据不一致")
    composite = strategy in {"economic", "three_eff", "self_research"}
    if plan.aggregation != ("equal_scores" if composite else "none"):
        raise ValueError("当前复合指标支持各项赋分后的等权平均，不支持平均原值或非等权平均")
    if plan.final_ranking_direction != ("descending" if composite else "not_applicable"):
        raise ValueError("复合指标按平均得分从高到低再次排名；单项指标不重复排名")
    if strategy == "economic":
        if plan.growth_denominator not in {"absolute_previous", "previous"} or plan.growth_preserve_sign is not True:
            raise ValueError("增幅支持以上年值或上年值绝对值为分母，结果须保留正负号")
    elif plan.growth_denominator != "not_applicable" or plan.growth_preserve_sign is not None:
        raise ValueError("当前指标没有同比增幅步骤，请确认业务口径")
    if plan.cap_percent is not None and (strategy != "self_research" or not 0 < plan.cap_percent <= 100):
        raise ValueError("占比封顶仅适用于自研收入占比，范围为大于0且不超过100%")
    return plan


def compile_plan(value, source: str, ranking: dict | None = None) -> dict:
    plan = validate_plan(value, source)
    settings = {**(ranking or {}), "direction": plan.ranking_direction}
    first = plan.first_score if plan.first_score is not None else settings.get("first_score", 10)
    decrement = plan.rank_decrement if plan.rank_decrement is not None else settings.get("decrement", .5)
    def finite(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    try:
        valid = finite(first) and first > 0 and finite(decrement) and decrement >= 0 and math.isfinite((first-(len(PRODUCTS)-1)*decrement)/first)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("首名分必须大于0、递减值必须非负，且均须在有限可计算范围内")
    settings.update(first_score=first, decrement=decrement)
    direction = "从低到高" if plan.ranking_direction == "ascending" else "从高到低"
    title = METRIC_NAMES[source]
    if plan.strategy == "economic":
        denominator = "上年值绝对值" if plan.growth_denominator == "absolute_previous" else "上年值"
        explanation = f"当年值与同比增幅分别{direction}排名赋分；增幅为当年与上年之差除以{denominator}，保留正负号；两项得分等权平均后，按平均分从高到低再次排名赋分。"
    elif plan.strategy in {"three_eff", "self_research"}:
        explanation = ("劳产、人效、元效" if source == "three_eff" else f"自研收入占比（{plan.cap_percent or 100:g}%封顶）与研发投入产出比（研发收入÷七项研发成本合计）") + f"分别{direction}排名赋分，得分等权平均后再按高分优先排名赋分。"
    else:
        explanation = f"{'使用按年度固定生成的模拟能力评测原值，' if source == 'ability' else ''}{title}{direction}排名赋分。"
    explanation += f"首名{first:g}分，每名递减{decrement:g}分；并列同名次、后续名次跳位。"
    year = specified_year(plan)
    if year is not None:
        explanation += f"本规则明确限定{year}年数据" + (f"，同比基数为{year-1}年。" if plan.strategy == "economic" else "。")
    rule = {"execution_plan": plan_value(plan), "normalized_summary": explanation,
            "ranking_direction": plan.ranking_direction, "time_window": plan.time_window, "tie_method": plan.tie_method,
            "parameters": {"first_score": plan.first_score, "rank_decrement": plan.rank_decrement,
                           "cap_percent": plan.cap_percent, "growth_denominator_uses_absolute_value": plan.growth_denominator == "absolute_previous" if plan.strategy == "economic" else None},
            "required_inputs": [{"name": ("上一年度" if item.period == "previous_year" else "评估年度") + field_label(item.field+":2025")[5:], "concept": item.field, "period": item.period, "required": True} for item in plan.inputs],
            "steps": [], "assumptions": [], "warnings": [], "confidence": 1}
    return {"ranking": settings, "final_ranking": {**settings, "direction": "descending"},
            "cap": (plan.cap_percent if plan.cap_percent is not None else 100)/100,
            "growth_denominator_absolute": plan.growth_denominator != "previous", "rule": rule}


def normalize_interpretation(value: dict) -> dict:
    value = deepcopy(value)
    if isinstance(value, dict) and isinstance(value.get("execution_plan"), dict):
        raw_plan = value["execution_plan"]
        raw_plan["version"] = CONTRACT
        inputs = raw_plan.get("inputs")
        if isinstance(inputs, list) and all(isinstance(item, dict) for item in inputs):
            for item in inputs:
                year = item.get("year")
                if isinstance(year, int) and not isinstance(year, bool) and 19 <= year <= 99:
                    item["year"] = 2000 + year
            periods = {item.get("period") for item in inputs if isinstance(item.get("period"), str)}
            if periods == {"current_year", "previous_year"}:
                raw_plan["time_window"] = "latest_two_years"
            elif periods in ({"current_year"}, set()):
                raw_plan["time_window"] = "latest_year"
            else:
                raw_plan["time_window"] = "custom"
        if "growth_result" in raw_plan:
            result = raw_plan.pop("growth_result")
            modes = {"signed_change": True, "absolute_change": False, "not_applicable": None}
            if not isinstance(result, str) or result not in modes:
                raise ValueError("AI 未明确增幅结果是否保留增长和下降方向")
            raw_plan["growth_preserve_sign"] = modes[result]
    try:
        interpretation = Interpretation.model_validate(value)
    except ValidationError as exc:
        raise ValueError("AI 未返回完整的结构化计算方案，请重试识别") from exc
    # Preserve unresolved plans so the API can return their concrete questions.
    plan = interpretation.execution_plan
    rule = {"execution_plan": plan_value(plan), "normalized_summary": interpretation.normalized_summary,
            "confidence": interpretation.confidence}
    return rule
