"""Model-owned coverage and result labels shared by the UI and exports."""
from __future__ import annotations

from .calculation_types import finite_number
from .constants import METRIC_NAMES, PRODUCTS
from .model_defaults import default_model_definition
from .model_engine import simulate
from .score_presentation import present_scores


def describe_result(definition: dict | None, result: dict) -> dict:
    definition = definition if definition is not None else default_model_definition()
    indicators = [item for item in definition.get("dimensions", []) if item.get("enabled", True)]
    scores = result.get("scores") or {}
    # A stored summary is a historical calculation, not a request to recompute.
    summary = result.get("model_summary")
    if summary is None:
        try:
            summary = simulate(definition, result)
        except ValueError:
            summary = {"rows": [], "ready": False, "complete_products": 0}
    simulated = set(result.get("simulated_metrics") or [])
    simulated.update(trace["metric"] for trace in result.get("traces", [])
                     if trace.get("simulated") or any("模拟" in step.get("label", "") for step in trace.get("steps", [])))
    entries = []
    for indicator in indicators:
        metrics = indicator.get("metric_keys") or []
        complete = bool(metrics) and all(finite_number((scores.get(p) or {}).get(m)) for p in PRODUCTS for m in metrics)
        entries.append({"key": indicator["key"], "label": indicator.get("label", indicator["key"]),
                        "weight": indicator.get("weight", 0), "metric_keys": metrics,
                        "complete": complete, "simulated": bool(simulated.intersection(metrics)),
                        "status": "已计算" if complete else "待完成"})
    complete_entries = [item for item in entries if item["complete"]]
    missing = [item["label"] for item in entries if not item["complete"]]
    simulated_labels = [item["label"] for item in entries if item["simulated"]]
    enabled_metrics = {metric for item in entries for metric in item["metric_keys"]}
    weight_total = sum(float(item["weight"]) for item in entries)
    covered = sum(float(item["weight"]) for item in complete_entries)
    ready = not missing and bool(entries) and bool(summary.get("ready"))
    state = "partial" if not ready else "trial" if simulated_labels else "complete"
    if state == "partial":
        note = f"分项测算；待完成：{'、'.join(missing) or '总分核对'}。总分与排名暂不生成。"
        if simulated_labels:
            note += f"{'、'.join(simulated_labels)}含模拟数据。"
    elif state == "trial":
        note = f"已生成试算总分与排名；{'、'.join(simulated_labels)}含模拟数据，不作为正式业务结论。"
    else:
        note = "已生成总分与排名；当前模型全部指标使用真实数据。"
    metric_entries = []
    for indicator in entries:
        for metric in indicator["metric_keys"]:
            metric_entries.append({"key": metric, "label": indicator["label"] if len(indicator["metric_keys"]) == 1 else METRIC_NAMES.get(metric, metric),
                                   "status": "已计算" if all(finite_number((scores.get(p) or {}).get(metric)) for p in PRODUCTS) else "待完成"})
    return {**result, **present_scores(definition, result, summary), "model_summary": summary, "indicators": entries, "metrics": metric_entries,
            "indicator_count": len(entries), "calculated_indicator_count": len(complete_entries),
            "covered_points": covered, "weight_total": weight_total, "missing_indicators": missing,
            "simulated_indicators": simulated_labels, "simulated_metrics": sorted(simulated.intersection(enabled_metrics)),
            "result_status": state, "status_note": note,
            "official_total_available": ready and not simulated_labels and abs(weight_total - 100) < 1e-9}
