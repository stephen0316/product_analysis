from __future__ import annotations

from collections import defaultdict
from typing import Any

from .constants import ECONOMIC_FIELDS, METRICS, METRIC_NAMES, PRODUCTS, THREE_EFF_FIELDS, expected_fields, field_label
from .ingest import extract_records


def competition_ranks(values: dict[str, float]) -> dict[str, int]:
    ordered = sorted(values.values(), reverse=True)
    return {product: ordered.index(value) + 1 for product, value in values.items()}


def rank_scores(values: dict[str, float]) -> tuple[dict[str, int], dict[str, float]]:
    ranks = competition_ranks(values)
    return ranks, {p: 10 - (rank - 1) * 0.5 for p, rank in ranks.items()}


def calculate(year: int, files: list[dict[str, Any]], source_choices: dict[str, str]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    by_field: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for f in files:
        records, file_issues = extract_records(f["path"], f["id"], f["role"], f["mapping"])
        issues.extend([{**issue, "file_id": f["id"]} for issue in file_issues])
        for record in records:
            by_field[record["field"]][record["product"]].append(record)

    selected: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    conflicts = []
    all_fields = [field for role in ("market", "finance", "three_eff") for field in expected_fields(role, year)]
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
    status: dict[str, str] = {key: "待完成" if group is None else "数据待核对" for key, _, group in METRICS}
    _score_single("market_share", f"market_share:{year}", selected, scores, traces, status)
    for metric in ECONOMIC_FIELDS:
        _score_economic(metric, year, selected, scores, traces, status, issues)
    _score_three_eff(year, selected, scores, traces, status)
    return {
        "year": year,
        "products": PRODUCTS,
        "metrics": [{"key": key, "label": label, "status": status[key]} for key, label, _ in METRICS],
        "scores": scores,
        "traces": traces,
        "issues": issues,
        "conflicts": conflicts,
        "source_choices": source_choices,
        "official_total_available": False,
        "covered_points": sum(10 for key, _, _ in METRICS if status[key] == "已计算"),
    }


def _complete(selected: dict, fields: list[str]) -> bool:
    return all(all(product in selected.get(field, {}) for product in PRODUCTS) for field in fields)


def _score_single(metric: str, field: str, selected: dict, scores: dict, traces: list, status: dict) -> None:
    if not _complete(selected, [field]):
        return
    values = {p: selected[field][p]["value"] for p in PRODUCTS}
    ranks, points = rank_scores(values)
    for p in PRODUCTS:
        scores[p][metric] = points[p]
        traces.append({"product": p, "metric": metric, "score": points[p], "rank": ranks[p], "steps": [{"label": field_label(field), "value": values[p], "rank": ranks[p], "score": points[p], "source": selected[field][p]}]})
    status[metric] = "已计算"


def _score_economic(metric: str, year: int, selected: dict, scores: dict, traces: list, status: dict, issues: list) -> None:
    previous, current = f"{metric}:{year-1}", f"{metric}:{year}"
    if not _complete(selected, [previous, current]):
        return
    zero_products = [p for p in PRODUCTS if selected[previous][p]["value"] == 0]
    if zero_products:
        for p in zero_products:
            issues.append({"level": "error", "code": "zero_base", "message": f"{p} 的{field_label(previous)}为 0，增幅无法计算", "field": previous, "product": p})
        return
    values = {p: selected[current][p]["value"] for p in PRODUCTS}
    changes = {p: (values[p] - selected[previous][p]["value"]) / selected[previous][p]["value"] for p in PRODUCTS}
    for p in PRODUCTS:
        if selected[previous][p]["value"] < 0:
            issues.append({"level": "warning", "code": "negative_base", "message": f"{p} 的{field_label(previous)}为负数，按客户既定相对增幅公式计算；请复核经营含义", "field": previous, "product": p})
    value_ranks, value_scores = rank_scores(values)
    growth_ranks, growth_scores = rank_scores(changes)
    averages = {p: (value_scores[p] + growth_scores[p]) / 2 for p in PRODUCTS}
    final_ranks, final_scores = rank_scores(averages)
    for p in PRODUCTS:
        scores[p][metric] = final_scores[p]
        traces.append({"product": p, "metric": metric, "score": final_scores[p], "rank": final_ranks[p], "average": averages[p], "steps": [
            {"label": field_label(previous), "value": selected[previous][p]["value"], "source": selected[previous][p]},
            {"label": field_label(current), "value": values[p], "rank": value_ranks[p], "score": value_scores[p], "source": selected[current][p]},
            {"label": "相对增幅", "value": changes[p], "rank": growth_ranks[p], "score": growth_scores[p], "formula": f"({field_label(current)}－{field_label(previous)})÷{field_label(previous)}"},
            {"label": "两项得分平均", "value": averages[p], "rank": final_ranks[p], "score": final_scores[p]},
        ]})
    status[metric] = "已计算"


def _score_three_eff(year: int, selected: dict, scores: dict, traces: list, status: dict) -> None:
    fields = [f"{metric}:{year}" for metric in THREE_EFF_FIELDS]
    if not _complete(selected, fields):
        return
    individual = []
    for field in fields:
        values = {p: selected[field][p]["value"] for p in PRODUCTS}
        ranks, points = rank_scores(values)
        individual.append((field, values, ranks, points))
    averages = {p: sum(item[3][p] for item in individual) / 3 for p in PRODUCTS}
    final_ranks, final_scores = rank_scores(averages)
    for p in PRODUCTS:
        scores[p]["three_eff"] = final_scores[p]
        steps = [{"label": field_label(field), "value": values[p], "rank": ranks[p], "score": points[p], "source": selected[field][p]} for field, values, ranks, points in individual]
        steps.append({"label": "三项得分平均", "value": averages[p], "rank": final_ranks[p], "score": final_scores[p]})
        traces.append({"product": p, "metric": "three_eff", "score": final_scores[p], "rank": final_ranks[p], "average": averages[p], "steps": steps})
    status["three_eff"] = "已计算"
