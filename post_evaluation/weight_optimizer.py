from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .constants import PRODUCTS


EPSILON = 1e-9


@dataclass(frozen=True)
class Evaluation:
    totals: tuple[float, ...]
    ranks: tuple[int, ...]
    target_rank: int
    target_gap: float
    max_other_movement: int
    total_other_movement: int


def _competition_ranks(totals: tuple[float, ...]) -> tuple[int, ...]:
    return tuple(1 + sum(value > total + EPSILON for value in totals) for total in totals)


def _target_gap(totals: tuple[float, ...], target_index: int, requested_rank: int, direction: str) -> float:
    target_total = totals[target_index]
    others = sorted((value for index, value in enumerate(totals) if index != target_index), reverse=True)
    if direction == "down":
        threshold_index = max(0, min(len(others) - 1, requested_rank - 2))
        return target_total - others[threshold_index]
    threshold_index = max(0, min(len(others) - 1, requested_rank - 1))
    return others[threshold_index] - target_total


def _dimension_matrix(definition: dict[str, Any], result: dict[str, Any]) -> tuple[list[dict[str, Any]], tuple[tuple[float, ...], ...]]:
    dimensions = [item for item in definition.get("dimensions", []) if item.get("enabled", True)]
    if not dimensions:
        raise ValueError("当前模型没有可调优的指标")
    scores = result.get("scores") or {}
    matrix: list[tuple[float, ...]] = []
    missing: list[str] = []
    for product in PRODUCTS:
        product_scores = scores.get(product) or {}
        row: list[float] = []
        for dimension in dimensions:
            metric_keys = dimension.get("metric_keys") or []
            absent = [key for key in metric_keys if product_scores.get(key) is None]
            if absent:
                missing.extend(f"{product}／{key}" for key in absent)
                row.append(0.0)
            else:
                row.append(sum(float(product_scores[key]) for key in metric_keys) / len(metric_keys))
        matrix.append(tuple(row))
    if missing:
        preview = "、".join(missing[:4])
        suffix = "等" if len(missing) > 4 else ""
        raise ValueError(f"当前批次评分数据不完整：{preview}{suffix}；请完成计算后再进行智能调优")
    return dimensions, tuple(matrix)


def _totals(matrix: tuple[tuple[float, ...], ...], weights: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(round(sum(score * weight for score, weight in zip(row, weights)) / 10.0, 9) for row in matrix)


def _evaluate(
    matrix: tuple[tuple[float, ...], ...],
    weights: tuple[float, ...],
    target_index: int,
    requested_rank: int,
    direction: str,
    baseline_ranks: tuple[int, ...],
) -> Evaluation:
    totals = _totals(matrix, weights)
    ranks = _competition_ranks(totals)
    other_movements = [abs(rank - baseline_ranks[index]) for index, rank in enumerate(ranks) if index != target_index]
    return Evaluation(
        totals=totals,
        ranks=ranks,
        target_rank=ranks[target_index],
        target_gap=_target_gap(totals, target_index, requested_rank, direction),
        max_other_movement=max(other_movements, default=0),
        total_other_movement=sum(other_movements),
    )


def _goal_achieved(rank: int, requested_rank: int, direction: str) -> bool:
    return rank >= requested_rank if direction == "down" else rank <= requested_rank


def _progress_distance(rank: int, requested_rank: int, direction: str) -> int:
    return max(0, requested_rank - rank) if direction == "down" else max(0, rank - requested_rank)


def _state_priority(
    evaluation: Evaluation,
    weights: tuple[float, ...],
    base_weights: tuple[float, ...],
    requested_rank: int,
    direction: str,
) -> tuple[float, ...]:
    distance = _progress_distance(evaluation.target_rank, requested_rank, direction)
    transferred = sum(abs(value - base) for value, base in zip(weights, base_weights)) / 2
    changed = sum(abs(value - base) > EPSILON for value, base in zip(weights, base_weights))
    return (
        float(distance),
        max(0.0, evaluation.target_gap),
        float(evaluation.max_other_movement),
        float(evaluation.total_other_movement),
        transferred,
        float(changed),
    )


def _candidate(
    candidate_id: str,
    label: str,
    weights: tuple[float, ...],
    evaluation: Evaluation,
    dimensions: list[dict[str, Any]],
    base_weights: tuple[float, ...],
    baseline_totals: tuple[float, ...],
    baseline_ranks: tuple[int, ...],
    requested_rank: int,
    direction: str,
) -> dict[str, Any]:
    changes = []
    for dimension, old, new in zip(dimensions, base_weights, weights):
        if abs(old - new) <= EPSILON:
            continue
        changes.append({
            "key": dimension.get("key"),
            "label": dimension.get("label"),
            "from": old,
            "to": new,
            "delta": round(new - old, 6),
        })
    increases = "、".join(item["label"] for item in changes if item["delta"] > 0) or "无"
    decreases = "、".join(item["label"] for item in changes if item["delta"] < 0) or "无"
    rows = [{
        "product": product,
        "baseline_total": round(baseline_totals[index], 6),
        "baseline_rank": baseline_ranks[index],
        "total": round(evaluation.totals[index], 6),
        "rank": evaluation.ranks[index],
        "position_change": evaluation.ranks[index] - baseline_ranks[index],
    } for index, product in enumerate(PRODUCTS)]
    return {
        "id": candidate_id,
        "label": label,
        "achieved": _goal_achieved(evaluation.target_rank, requested_rank, direction),
        "target_rank_after": evaluation.target_rank,
        "requested_rank": requested_rank,
        "weights": {dimension.get("key"): weight for dimension, weight in zip(dimensions, weights)},
        "weight_changes": changes,
        "changed_dimensions": len(changes),
        "max_other_rank_change": evaluation.max_other_movement,
        "total_rank_movement": evaluation.total_other_movement,
        "explanation": f"提高{increases}的权重，降低{decreases}的权重；所有产品继续使用同一套规则重新计算。",
        "rows": rows,
    }


def optimize_weights(
    definition: dict[str, Any],
    result: dict[str, Any] | None,
    goal: dict[str, Any],
    *,
    max_weight_change: float = 5,
    max_other_rank_change: int = 3,
    step: float = 1,
) -> dict[str, Any]:
    if result is None:
        raise ValueError("当前批次尚未生成评分结果")
    target_product = goal.get("target_product")
    direction = goal.get("direction")
    positions = goal.get("positions")
    if target_product not in PRODUCTS or direction not in {"up", "down"} or not isinstance(positions, int):
        raise ValueError("排名调整目标不完整")
    if step <= 0 or max_weight_change < step:
        raise ValueError("权重调整约束无效")

    dimensions, matrix = _dimension_matrix(definition, result)
    base_weights = tuple(float(item.get("weight", 0)) for item in dimensions)
    if abs(sum(base_weights) - 100) > EPSILON:
        raise ValueError("当前模型权重合计不是 100，无法进行等额调优")
    target_index = PRODUCTS.index(target_product)
    baseline_totals = _totals(matrix, base_weights)
    baseline_ranks = _competition_ranks(baseline_totals)
    baseline_rank = baseline_ranks[target_index]
    requested_rank = min(len(PRODUCTS), baseline_rank + positions) if direction == "down" else max(1, baseline_rank - positions)
    if requested_rank == baseline_rank:
        raise ValueError("目标名次已到达排名边界，无法继续移动")

    lower = tuple(max(0.0, value - max_weight_change) for value in base_weights)
    upper = tuple(value + max_weight_change for value in base_weights)
    max_steps = max(1, min(25, int(round(max_weight_change * len(dimensions) / 2))))
    beam_size = 500
    base_evaluation = _evaluate(matrix, base_weights, target_index, requested_rank, direction, baseline_ranks)
    evaluations: dict[tuple[float, ...], Evaluation] = {base_weights: base_evaluation}
    beam = [base_weights]
    feasible: dict[tuple[float, ...], Evaluation] = {}
    best_attempts: dict[tuple[float, ...], Evaluation] = {}

    for _ in range(max_steps):
        neighbors: set[tuple[float, ...]] = set()
        for weights in beam:
            for source in range(len(weights)):
                if weights[source] - step < lower[source] - EPSILON:
                    continue
                for destination in range(len(weights)):
                    if source == destination or weights[destination] + step > upper[destination] + EPSILON:
                        continue
                    next_weights = list(weights)
                    next_weights[source] = round(next_weights[source] - step, 6)
                    next_weights[destination] = round(next_weights[destination] + step, 6)
                    state = tuple(next_weights)
                    if state not in evaluations:
                        neighbors.add(state)
        if not neighbors:
            break
        for state in neighbors:
            evaluation = _evaluate(matrix, state, target_index, requested_rank, direction, baseline_ranks)
            evaluations[state] = evaluation
            best_attempts[state] = evaluation
            if _goal_achieved(evaluation.target_rank, requested_rank, direction) and evaluation.max_other_movement <= max_other_rank_change:
                feasible[state] = evaluation
        beam = sorted(
            neighbors,
            key=lambda state: _state_priority(evaluations[state], state, base_weights, requested_rank, direction),
        )[:beam_size]
        if feasible and len(feasible) >= 18:
            break

    pool = feasible or best_attempts
    if not pool:
        pool = {base_weights: base_evaluation}
    minimal = min(pool, key=lambda state: (
        _progress_distance(pool[state].target_rank, requested_rank, direction),
        sum(abs(value - base) for value, base in zip(state, base_weights)),
        pool[state].max_other_movement,
        pool[state].total_other_movement,
    ))
    stable = min(pool, key=lambda state: (
        _progress_distance(pool[state].target_rank, requested_rank, direction),
        pool[state].max_other_movement,
        pool[state].total_other_movement,
        sum(abs(value - base) for value, base in zip(state, base_weights)),
    ))
    focused = min(pool, key=lambda state: (
        _progress_distance(pool[state].target_rank, requested_rank, direction),
        sum(abs(value - base) > EPSILON for value, base in zip(state, base_weights)),
        sum(abs(value - base) for value, base in zip(state, base_weights)),
        pool[state].total_other_movement,
    ))
    selections: list[tuple[str, str, tuple[float, ...]]] = []
    for candidate_id, label, state in (
        ("minimal", "最小调整", minimal),
        ("stable", "影响更稳", stable),
        ("focused", "集中调整", focused),
    ):
        if state not in [item[2] for item in selections]:
            selections.append((candidate_id, label, state))
    if len(selections) < 3:
        ordered = sorted(pool, key=lambda state: _state_priority(pool[state], state, base_weights, requested_rank, direction))
        for state in ordered:
            if state in [item[2] for item in selections]:
                continue
            selections.append((f"alternative-{len(selections) + 1}", "备选方案", state))
            if len(selections) == 3:
                break

    candidates = []
    for candidate_id, label, state in selections:
        candidate = _candidate(
            candidate_id, label, state, pool[state], dimensions, base_weights,
            baseline_totals, baseline_ranks, requested_rank, direction,
        )
        candidate["target_rank_before"] = baseline_rank
        candidates.append(candidate)
    achieved = bool(feasible)
    if achieved:
        message = f"已找到 {len(feasible)} 组满足约束的权重组合，展示其中差异较明确的方案。"
    else:
        best_rank = candidates[0]["target_rank_after"] if candidates else baseline_rank
        message = f"在当前约束内未完全达到目标；最佳尝试可将目标产品从第 {baseline_rank} 名调整到第 {best_rank} 名。可适度放宽权重或其他产品波动上限后重试。"
    return {
        "goal": {
            **goal,
            "baseline_rank": baseline_rank,
            "requested_rank": requested_rank,
        },
        "constraints": {
            "weight_total": 100,
            "max_weight_change": max_weight_change,
            "max_other_rank_change": max_other_rank_change,
            "step": step,
        },
        "feasible": achieved,
        "message": message,
        "candidates": candidates,
    }
