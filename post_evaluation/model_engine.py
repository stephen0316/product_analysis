from __future__ import annotations

from typing import Any

from .constants import PRODUCTS


ALLOWED_AGGREGATIONS = {"average_scaled"}


def validate_definition(definition: dict[str, Any], *, require_100: bool = False) -> list[str]:
    errors: list[str] = []
    ranking = definition.get("ranking") or {}
    if ranking.get("method") != "competition":
        errors.append("首期仅支持竞争排名")
    if not isinstance(ranking.get("first_score"), (int, float)) or ranking.get("first_score", 0) <= 0:
        errors.append("首名分数必须大于 0")
    if not isinstance(ranking.get("decrement"), (int, float)) or ranking.get("decrement", -1) < 0:
        errors.append("名次递减值不能小于 0")

    dimensions = definition.get("dimensions")
    if not isinstance(dimensions, list) or not dimensions:
        return [*errors, "至少需要一个维度"]
    keys: set[str] = set()
    metric_owners: dict[str, str] = {}
    total_weight = 0.0
    for index, dimension in enumerate(dimensions, 1):
        key = str(dimension.get("key") or "").strip()
        label = str(dimension.get("label") or "").strip()
        if not key:
            errors.append(f"第 {index} 个维度缺少标识")
        elif key in keys:
            errors.append(f"维度标识重复：{key}")
        keys.add(key)
        if not label:
            errors.append(f"第 {index} 个维度缺少名称")
        if dimension.get("aggregation") not in ALLOWED_AGGREGATIONS:
            errors.append(f"{label or key} 使用了不支持的汇总方式")
        metrics = dimension.get("metric_keys")
        if not isinstance(metrics, list) or not all(isinstance(item, str) and item.strip() for item in metrics):
            errors.append(f"{label or key} 缺少内部计算标识")
        else:
            for metric in metrics:
                if metric in metric_owners:
                    errors.append(f"维度内部计算标识重复：{metric} 已用于 {metric_owners[metric]}")
                else:
                    metric_owners[metric] = label or key
        weight = dimension.get("weight")
        if not isinstance(weight, (int, float)) or weight < 0:
            errors.append(f"{label or key} 的权重必须为非负数")
            continue
        total_weight += float(weight)
    if require_100 and abs(total_weight - 100.0) > 1e-9:
        errors.append(f"维度权重合计须为 100，当前为 {total_weight:g}")
    return errors


def enabled_weight(definition: dict[str, Any]) -> float:
    return sum(float(item.get("weight", 0)) for item in definition.get("dimensions", []))


def simulate(definition: dict[str, Any], result: dict[str, Any] | None) -> dict[str, Any]:
    scores = (result or {}).get("scores") or {}
    dimensions = definition.get("dimensions", [])
    rows: list[dict[str, Any]] = []
    for product in PRODUCTS:
        product_scores = scores.get(product) or {}
        total = 0.0
        complete = True
        dimension_rows = []
        missing: list[str] = []
        for dimension in dimensions:
            metric_keys = dimension.get("metric_keys") or []
            absent = [key for key in metric_keys if product_scores.get(key) is None]
            if absent:
                complete = False
                missing.extend(absent)
                dimension_rows.append({"key": dimension.get("key"), "label": dimension.get("label"), "score": None, "missing": absent})
                continue
            average = sum(float(product_scores[key]) for key in metric_keys) / len(metric_keys)
            weighted_score = average * float(dimension.get("weight", 0)) / 10.0
            total += weighted_score
            dimension_rows.append({"key": dimension.get("key"), "label": dimension.get("label"), "score": weighted_score, "missing": []})
        rows.append({
            "product": product,
            "total": round(total, 6) if complete else None,
            "partial_total": round(total, 6),
            "complete": complete,
            "missing_metrics": sorted(set(missing)),
            "dimensions": dimension_rows,
        })
    complete_values = {row["product"]: row["total"] for row in rows if row["complete"]}
    ordered = sorted(complete_values.values(), reverse=True)
    for row in rows:
        row["rank"] = ordered.index(row["total"]) + 1 if row["complete"] else None
    return {
        "rows": rows,
        "complete_products": len(complete_values),
        "weight_total": enabled_weight(definition),
        "ready": len(complete_values) == len(PRODUCTS),
    }


def compare(candidate: dict[str, Any], baseline: dict[str, Any] | None) -> dict[str, Any]:
    baseline_by_product = {row["product"]: row for row in (baseline or {}).get("rows", [])}
    rows = []
    for row in candidate["rows"]:
        old = baseline_by_product.get(row["product"])
        rows.append({
            **row,
            "baseline_total": old.get("total") if old else None,
            "baseline_rank": old.get("rank") if old else None,
            "score_delta": row["total"] - old["total"] if row["total"] is not None and old and old.get("total") is not None else None,
            "rank_delta": old["rank"] - row["rank"] if row["rank"] is not None and old and old.get("rank") is not None else None,
        })
    return {**candidate, "rows": rows}
