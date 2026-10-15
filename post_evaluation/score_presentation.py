"""Read-only explanations and display semantics for scored model indicators."""
from .calculation_types import bindings, compile_rule, finite_number
from .constants import ECONOMIC_FIELDS, METRIC_NAMES


def step_format(type_id: str, index: int, step: dict) -> dict:
    field = str((step.get('source') or {}).get('field', '')).split(':')[0]
    percent = field in {'market_share', 'self_owned_ratio', 'gross_margin', 'operating_margin'}
    percent |= type_id == 'market_share' or (type_id in ECONOMIC_FIELDS and (index == 2 or (type_id in {'gross_margin', 'operating_margin'} and index in {0, 1})))
    percent |= type_id == 'self_research' and index == 0
    if percent:
        return {'format': 'percent', 'unit': ''}
    if (type_id in ECONOMIC_FIELDS and index == 3) or (type_id == 'three_eff' and index == 3) or (type_id == 'self_research' and index == 11):
        return {'format': 'score', 'unit': '分'}
    if type_id == 'self_research' and index == 10:
        return {'format': 'ratio', 'unit': '倍'}
    unit = (step.get('source') or {}).get('unit') or step.get('unit') or ''
    money = field in {'revenue', 'gross_profit', 'operating_profit', 'research_revenue'} or field.startswith('research_cost_')
    money |= (type_id in {'revenue', 'gross_profit', 'operating_profit'} and index in {0, 1}) or (type_id == 'self_research' and 1 <= index <= 9)
    return {'format': 'number', 'unit': unit or ('单位未标注' if money else '')}


def step_label(indicator: dict, type_id: str, index: int, step: dict, year: int) -> str:
    label = str(indicator.get('label') or indicator['key'])
    base = label
    for separator in ('／', '/'):
        if separator in base:
            base = base.split(separator, 1)[0].strip()
            break
    if type_id == 'market_share' or (type_id == 'quality' and index == 0):
        return f'{year}年{label}'
    if type_id in ECONOMIC_FIELDS:
        return [f'{year-1}年{base}', f'{year}年{base}', f'{year-1}-{year}年{base}增幅', f'{base}/增幅平均分'][index] if index < 4 else step.get('label', '')
    if type_id == 'ability' and index == 0:
        return f'{year}年{label}（模拟值）'
    if type_id == 'self_research' and index == 9:
        return '研发总成本'
    if type_id == 'self_research' and index == 11:
        return f'{label}平均分'
    raw = step.get('label') or f'步骤{index+1}'
    return f'{label}平均分' if raw in {'两项得分平均', '三项得分平均'} else raw


def present_scores(definition: dict, result: dict, summary: dict) -> dict:
    """Use the batch model and stored contribution; never rewrite historical scores."""
    dimensions = [d for d in definition.get('dimensions', []) if d.get('enabled', True)]
    raw = result.get('scores') or {}
    traces = {(t['product'], t['metric']): t for t in result.get('traces', [])}
    summary_rows = {r['product']: r for r in summary.get('rows', [])}
    explanations, by_trace = {}, {}
    for product, product_scores in raw.items():
        rows = []
        stored = {d['key']: d.get('score') for d in summary_rows.get(product, {}).get('dimensions', [])}
        for dimension in dimensions:
            components = []
            try:
                bound = bindings(dimension)
            except ValueError:
                bound = []
            for metric, type_id in bound:
                trace = traces.get((product, metric), {})
                base = trace.get('first_score')
                if not finite_number(base) or base <= 0:
                    try:
                        base = compile_rule(dimension, type_id, definition.get('ranking'), executable=False)['ranking'].get('first_score', 10)
                    except ValueError:
                        base = None
                score = product_scores.get(metric)
                ratio = score / base if finite_number(score) and finite_number(base) and base > 0 else None
                components.append({'metric': metric, 'label': dimension['label'] if len(bound) == 1 else METRIC_NAMES.get(type_id, metric), 'raw_score': score, 'first_score': base, 'ratio': ratio})
            weight = dimension.get('weight', 0)
            contribution = stored.get(dimension['key'])
            ratio = contribution / weight if finite_number(contribution) and finite_number(weight) and weight > 0 else None
            row = {'key': dimension['key'], 'label': dimension['label'], 'weight': weight, 'weighted_score': contribution,
                   'ratio': ratio, 'components': components, 'metric_keys': dimension.get('metric_keys', [])}
            rows.append(row)
            for metric, type_id in bound:
                by_trace[(product, metric)] = (row, dimension, type_id)
        explanations[product] = rows
    enriched = []
    for original in result.get('traces', []):
        context = by_trace.get((original['product'], original['metric']))
        if not context:
            enriched.append(original)
            continue
        row, dimension, type_id = context
        raw_steps = original.get('steps', [])
        steps = [{**step, **step_format(type_id, index, step), 'display_label': step_label(dimension, type_id, index, step, int(result.get('year', 2025)))} for index, step in enumerate(raw_steps)]
        if type_id == 'self_research' and len(steps) > 9:
            cost_units = {step_format(type_id, index, raw_steps[index])['unit'] for index in range(2, 9)}
            if len(cost_units) == 1:
                steps[9]['unit'] = cost_units.pop()
        enriched.append({**original, 'indicator_label': row['label'], 'contribution': row, 'steps': steps})
    return {'indicator_scores': explanations, 'traces': enriched}
