from __future__ import annotations

from collections import defaultdict
import random
from typing import Any

from .constants import (
    ECONOMIC_FIELDS,
    PRODUCTS,
    SELF_RESEARCH_COST_FIELDS,
    THREE_EFF_FIELDS,
    field_label,
)
from .ingest import extract_records
from .calculation_types import bindings, compile_rule, raw_fields
from .semantic_rules import read_plan, specified_year
from .model_defaults import default_model_definition
from .model_engine import validate_definition
from .result_state import describe_result


def competition_ranks(values: dict[str, float], direction: str = "descending") -> dict[str, int]:
    ordered = sorted(values.values(), reverse=direction != "ascending")
    return {product: ordered.index(value) + 1 for product, value in values.items()}


def rank_scores(values: dict[str, float], ranking: dict[str, Any] | None = None) -> tuple[dict[str, int], dict[str, float]]:
    ranking = ranking or {}
    first_score = float(ranking.get("first_score", 10))
    decrement = float(ranking.get("decrement", 0.5))
    ranks = competition_ranks(values, ranking.get("direction", "descending"))
    return ranks, {p: first_score - (rank - 1) * decrement for p, rank in ranks.items()}


def calculate(year: int, files: list[dict[str, Any]], source_choices: dict[str, str], model_definition: dict[str, Any] | None = None) -> dict[str, Any]:
    model_definition = model_definition if model_definition is not None else default_model_definition()
    errors = validate_definition(model_definition)
    if errors:
        raise ValueError("；".join(errors))
    ranking = model_definition.get("ranking") or {}
    dimensions = [item for item in model_definition["dimensions"] if item.get("enabled", True)]
    compiled = [(output, type_id, compile_rule(item, type_id, ranking))
                for item in dimensions for output, type_id in bindings(item)]
    for output, _, settings in compiled:
        value = settings["rule"].get("execution_plan")
        fixed_year = specified_year(read_plan(value)) if value else None
        if fixed_year is not None and fixed_year != year:
            raise ValueError(f"指标{output}的规则明确限定{fixed_year}年，当前批次数据年度为{year}年；请使用对应年度批次或调整规则")
    issues: list[dict[str, Any]] = []
    by_field: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    dimensions_by_key = {item["key"]: [type_id for _, type_id in bindings(item)] for item in dimensions}
    # Older local batches stored metric keys directly.  Keep those records
    # calculable while new uploads use the model's business-dimension keys.
    selected_metric_keys = [
        metric
        for file in files
        for target in file.get("targets", [])
        for metric in dimensions_by_key.get(target, [target])
    ]
    all_fields = list(dict.fromkeys(field for type_id in selected_metric_keys for field in raw_fields(type_id, year)))
    for f in files:
        records, file_issues = extract_records(f["path"], f["id"], f["role"], f["mapping"])
        issues.extend({**issue, "file_id": f["id"]} for issue in file_issues)
        for record in records:
            by_field[record["field"]][record["product"]].append(record)

    selected: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    conflicts = []
    for field in all_fields:
        for product in PRODUCTS:
            candidates = by_field[field].get(product, [])
            if not candidates:
                issues.append({"level": "error", "code": "missing", "message": f"缺少 {product} 的{field_label(field)}", "field": field, "product": product})
                continue
            distinct = {round(c["value"], 12) for c in candidates}
            same_file_counts = defaultdict(int)
            for candidate in candidates:
                same_file_counts[candidate["file_id"]] += 1
            if any(count > 1 for count in same_file_counts.values()):
                issues.append({"level": "error", "code": "duplicate", "message": f"{product} 的{field_label(field)}在同一来源重复", "field": field, "product": product})
                continue
            choice = source_choices.get(field)
            if len(distinct) > 1 and not choice:
                conflicts.append({"field": field, "product": product, "candidates": candidates})
                issues.append({"level": "error", "code": "conflict", "message": f"{product} 的{field_label(field)}存在不同来源数值", "field": field, "product": product})
                continue
            chosen = next((c for c in candidates if c["file_id"] == choice), None) if choice else candidates[0]
            if chosen is None:
                issues.append({"level": "error", "code": "choice_missing", "message": f"{product} 的{field_label(field)}在指定来源中不存在", "field": field, "product": product})
                continue
            selected[field][product] = chosen

    scores: dict[str, dict[str, Any]] = {p: {} for p in PRODUCTS}
    traces: list[dict[str, Any]] = []
    simulated_metrics = []
    for output, type_id, settings in compiled:
        # Each indicator owns an execution, so instances may use different rules
        # over the same source without overwriting one another's scores.
        indicator_scores, indicator_traces, status = {p: {} for p in PRODUCTS}, [], {type_id: "待完成"}
        rank = settings["ranking"]
        final_rank = settings["final_ranking"]
        if type_id in {"market_share", "quality"}:
            _score_single(type_id, f"{type_id}:{year}", selected, indicator_scores, indicator_traces, status, rank)
        elif type_id in ECONOMIC_FIELDS:
            _score_economic(type_id, year, selected, indicator_scores, indicator_traces, status, issues, rank,
                            settings["growth_denominator_absolute"], final_rank)
        elif type_id == "three_eff":
            _score_three_eff(year, selected, indicator_scores, indicator_traces, status, rank, final_rank)
        elif type_id == "self_research":
            _score_self_research(year, selected, indicator_scores, indicator_traces, status, issues, rank, final_rank, settings["cap"])
        elif type_id == "ability":
            _score_mock_ability(year, indicator_scores, indicator_traces, status, rank)
            simulated_metrics.append(output)
        for product in PRODUCTS:
            if type_id in indicator_scores[product]:
                scores[product][output] = indicator_scores[product][type_id]
        for trace in indicator_traces:
            traces.append({**trace, "metric": output, "calculation_type": type_id,
                           "simulated": type_id == "ability", "ranking_direction": rank["direction"],
                           "first_score": rank.get("first_score", 10), "rank_decrement": rank.get("decrement", .5)})
    return describe_result(model_definition, {
        "year": year,
        "products": PRODUCTS,
        "scores": scores,
        "traces": traces,
        "issues": issues,
        "conflicts": conflicts,
        "source_choices": source_choices,
        "simulated_metrics": simulated_metrics,
    })


def _complete(selected: dict, fields: list[str]) -> bool:
    return all(all(product in selected.get(field, {}) for product in PRODUCTS) for field in fields)


def _score_single(metric: str, field: str, selected: dict, scores: dict, traces: list, status: dict, ranking: dict | None = None) -> None:
    if not _complete(selected, [field]):
        return
    values = {p: selected[field][p]["value"] for p in PRODUCTS}
    ranks, points = rank_scores(values, ranking)
    for p in PRODUCTS:
        scores[p][metric] = points[p]
        traces.append({"product": p, "metric": metric, "score": points[p], "rank": ranks[p], "steps": [{"label": field_label(field), "value": values[p], "rank": ranks[p], "score": points[p], "source": selected[field][p]}]})
    status[metric] = "已计算"


def _score_mock_ability(year: int, scores: dict, traces: list, status: dict, ranking: dict | None = None) -> None:
    """Create stable placeholder values until an ability-assessment source is available.

    A local seeded generator keeps recalculation auditable: the same evaluation
    year always produces the same unique raw value for each product.
    """
    generator = random.Random(f"ability-assessment:{year}")
    values = dict(zip(PRODUCTS, generator.sample(range(60, 101), len(PRODUCTS))))
    ranks, points = rank_scores(values, ranking)
    for product in PRODUCTS:
        scores[product]["ability"] = points[product]
        traces.append({
            "product": product,
            "metric": "ability",
            "score": points[product],
            "rank": ranks[product],
            "steps": [{
                "label": "模拟能力测评值",
                "value": values[product],
                "rank": ranks[product],
                "score": points[product],
                "formula": f"系统模拟占位值；按评估年份 {year} 固定生成，正式数据接入后替换",
            }],
        })
    status["ability"] = "已计算"


def _score_economic(metric: str, year: int, selected: dict, scores: dict, traces: list, status: dict, issues: list, ranking: dict | None = None, growth_denominator_absolute: bool = True, final_ranking: dict | None = None) -> None:
    previous, current = f"{metric}:{year-1}", f"{metric}:{year}"
    if not _complete(selected, [previous, current]):
        return
    zero_products = [p for p in PRODUCTS if selected[previous][p]["value"] == 0]
    if zero_products:
        for p in zero_products:
            issues.append({"level": "error", "code": "zero_base", "message": f"{p} 的{field_label(previous)}为 0，增幅无法计算", "field": previous, "product": p})
        return
    values = {p: selected[current][p]["value"] for p in PRODUCTS}
    changes = {
        p: (values[p] - selected[previous][p]["value"])
        / (abs(selected[previous][p]["value"]) if growth_denominator_absolute else selected[previous][p]["value"])
        for p in PRODUCTS
    }
    for p in PRODUCTS:
        if selected[previous][p]["value"] < 0:
            denominator_note = "绝对值分母" if growth_denominator_absolute else "原始分母"
            issues.append({"level": "warning", "code": "negative_base", "message": f"{p} 的{field_label(previous)}为负数，增幅按{denominator_note}计算并保留正负号", "field": previous, "product": p})
    value_ranks, value_scores = rank_scores(values, ranking)
    growth_ranks, growth_scores = rank_scores(changes, ranking)
    averages = {p: (value_scores[p] + growth_scores[p]) / 2 for p in PRODUCTS}
    final_ranks, final_scores = rank_scores(averages, final_ranking or {**(ranking or {}), "direction": "descending"})
    for p in PRODUCTS:
        scores[p][metric] = final_scores[p]
        traces.append({"product": p, "metric": metric, "score": final_scores[p], "rank": final_ranks[p], "average": averages[p], "steps": [
            {"label": field_label(previous), "value": selected[previous][p]["value"], "source": selected[previous][p]},
            {"label": field_label(current), "value": values[p], "rank": value_ranks[p], "score": value_scores[p], "source": selected[current][p]},
            {"label": "增幅", "value": changes[p], "rank": growth_ranks[p], "score": growth_scores[p], "formula": f"({field_label(current)}－{field_label(previous)})÷" + (f"ABS({field_label(previous)})" if growth_denominator_absolute else field_label(previous))},
            {"label": "两项得分平均", "value": averages[p], "rank": final_ranks[p], "score": final_scores[p]},
        ]})
    status[metric] = "已计算"


def _score_three_eff(year: int, selected: dict, scores: dict, traces: list, status: dict, ranking: dict | None = None, final_ranking: dict | None = None) -> None:
    fields = [f"{metric}:{year}" for metric in THREE_EFF_FIELDS]
    if not _complete(selected, fields):
        return
    individual = []
    for field in fields:
        values = {p: selected[field][p]["value"] for p in PRODUCTS}
        ranks, points = rank_scores(values, ranking)
        individual.append((field, values, ranks, points))
    averages = {p: sum(item[3][p] for item in individual) / 3 for p in PRODUCTS}
    final_ranks, final_scores = rank_scores(averages, final_ranking or {**(ranking or {}), "direction": "descending"})
    for p in PRODUCTS:
        scores[p]["three_eff"] = final_scores[p]
        steps = [{"label": field_label(field), "value": values[p], "rank": ranks[p], "score": points[p], "source": selected[field][p]} for field, values, ranks, points in individual]
        steps.append({"label": "三项得分平均", "value": averages[p], "rank": final_ranks[p], "score": final_scores[p]})
        traces.append({"product": p, "metric": "three_eff", "score": final_scores[p], "rank": final_ranks[p], "average": averages[p], "steps": steps})
    status["three_eff"] = "已计算"


def _score_self_research(year: int, selected: dict, scores: dict, traces: list, status: dict, issues: list, ranking: dict | None = None, final_ranking: dict | None = None, cap: float = 1.0) -> None:
    ratio_field = f"self_owned_ratio:{year}"
    revenue_field = f"research_revenue:{year}"
    cost_fields = [f"{field}:{year}" for field in SELF_RESEARCH_COST_FIELDS]
    if not _complete(selected, [ratio_field, revenue_field, *cost_fields]):
        return

    ratios = {product: min(selected[ratio_field][product]["value"], cap) for product in PRODUCTS}
    ratio_ranks, ratio_scores = rank_scores(ratios, ranking)
    costs = {
        product: sum(selected[field][product]["value"] for field in cost_fields)
        for product in PRODUCTS
    }
    zero_products = [product for product, value in costs.items() if value == 0]
    if zero_products:
        for product in zero_products:
            issues.append({
                "level": "error",
                "code": "zero_cost",
                "message": f"{product} 的研发成本合计为 0，研发投入产出比无法计算",
                "field": revenue_field,
                "product": product,
            })
        return

    roi = {product: selected[revenue_field][product]["value"] / costs[product] for product in PRODUCTS}
    roi_ranks, roi_scores = rank_scores(roi, ranking)
    averages = {product: (ratio_scores[product] + roi_scores[product]) / 2 for product in PRODUCTS}
    final_ranks, final_scores = rank_scores(averages, final_ranking or {**(ranking or {}), "direction": "descending"})

    for product in PRODUCTS:
        scores[product]["self_research"] = final_scores[product]
        ratio_source = selected[ratio_field][product]
        raw_ratio = ratio_source["value"]
        steps = [{
            "label": field_label(ratio_field),
            "value": ratios[product],
            "rank": ratio_ranks[product],
            "score": ratio_scores[product],
            "formula": f"MIN(原始占比, {cap:g})" if raw_ratio > cap else None,
            "source": ratio_source,
        }, {
            "label": field_label(revenue_field),
            "value": selected[revenue_field][product]["value"],
            "source": selected[revenue_field][product],
        }]
        steps.extend({
            "label": field_label(field),
            "value": selected[field][product]["value"],
            "source": selected[field][product],
        } for field in cost_fields)
        steps.extend([{
            "label": "研发总成本",
            "value": costs[product],
            "formula": "E～K 项研发成本之和",
        }, {
            "label": "研发投入产出比",
            "value": roi[product],
            "rank": roi_ranks[product],
            "score": roi_scores[product],
            "formula": "研发收入÷研发总成本",
        }, {
            "label": "自研占比/ROI平均分",
            "value": averages[product],
            "rank": final_ranks[product],
            "score": final_scores[product],
        }])
        traces.append({
            "product": product,
            "metric": "self_research",
            "score": final_scores[product],
            "rank": final_ranks[product],
            "average": averages[product],
            "steps": steps,
        })
    status["self_research"] = "已计算"
