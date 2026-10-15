"""Local recognition of straightforward business ranking language."""
from __future__ import annotations

import ast
import math
import re
import unicodedata


SUBJECTS = {
    "market_share": ("市场份额", "行业份额", "份额"),
    "quality": ("产品推广满意度", "客户满意度", "满意度", "品质服务"),
    "ability": ("原始评测分", "能力评测", "能力测评", "评测得分", "评测分"),
}
ASCENDING = r"升序|从小到大|从低到高|由小到大|由低到高|越小(?:越好|越靠前|排名越靠前|排名越高|得分越高|分数越高)|越低(?:越好|越靠前|排名越靠前|排名越高|得分越高|分数越高)"
DESCENDING = r"降序|从大到小|从高到低|由大到小|由高到低|越大(?:越好|越靠前|排名越靠前|排名越高|得分越高|分数越高)|越高(?:越好|越靠前|排名越靠前|排名越高|得分越高|分数越高)"


def ranking_intent(type_id: str, summary: str) -> dict | None:
    if type_id not in SUBJECTS:
        return None
    body = re.sub(r"\s+", "", unicodedata.normalize("NFKC", summary))
    body = re.sub(r"[，,。;；:：、]", "", body)
    ascending, descending = bool(re.search(ASCENDING, body)), bool(re.search(DESCENDING, body))
    if ascending and descending:
        return None
    if not ascending and not descending and not re.search(r"排名|排序", body):
        return None
    params = {"first_score": None, "rank_decrement": None, "cap_percent": None,
              "growth_denominator_uses_absolute_value": None}
    first = r"(?:第一名|第1名|首名|首位)(?:得|给|获|为|是)?(?:分数(?:为|是)?)?(\d+(?:\.\d+)?)分?"
    decrement = r"(?:每(?:下降|降低|后移|往后|后退|降)?(?:一|1)?(?:个)?(?:名次|名)(?:次)?(?:递减|减少|减|扣)|递减)(\d+(?:\.\d+)?)分?"
    for pattern, key in ((first, "first_score"), (decrement, "rank_decrement")):
        matches = list(re.finditer(pattern, body))
        if len({float(match[1]) for match in matches}) > 1:
            return None
        if matches:
            params[key] = float(matches[0][1])
            body = re.sub(pattern, "", body)
    # These clauses describe the already-supported competition tie method.
    body = re.sub(r"竞争排名|并列同名次[、,]?同分[、,]?后续(?:名次)?跳位|并列同分同名次[、,]?后续(?:名次)?跳位", "", body)
    body = re.sub(ASCENDING + "|" + DESCENDING, "", body)
    for subject in SUBJECTS[type_id]:
        body = body.replace(subject, "")
    if type_id == "ability":
        body = re.sub(r"当前允许(?:占位|模拟)数据", "", body)
    # Removing a closed vocabulary leaves unsupported conditions visible rather
    # than accepting every sentence which merely contains '排名'.
    words = ("按名次赋分", "按名次给分", "分配得分", "评估年度", "最新年度", "本年度", "原始评测分",
             "原始数值", "原始值", "当年", "本年", "最新", "年度", "名次", "进行", "大小", "依次",
             "分别", "按照", "根据", "然后", "之后", "顺序", "排序", "排名", "赋分", "评分", "得分",
             "打分", "数值", "计算", "取", "按", "以", "对", "将", "的", "先", "再", "并", "分", "值")
    for word in words:
        body = body.replace(word, "")
    if re.sub(r"[，,。.;；:：、]", "", body):
        return None
    return {"ranking_direction": "ascending" if ascending else "descending", "parameters": params}


def unsupported_claim(summary: str) -> bool:
    return bool(re.search(r"(?:直接|都|统一)(?:得|给|获)?满分|额外加分|人工加分", summary))


def growth_formula(formula: str, subject: str = "") -> bool | None:
    """Read the mathematical structure, never execute the parser's expression.

    Returns whether the denominator is absolute; None means unsupported.
    """
    text = unicodedata.normalize("NFKC", formula).replace("÷", "/").replace("×", "*")
    text = re.sub(r"\s+", "", text).split("=", 1)[-1]
    aliases = {
        "current": "current", "current_value": "current", "current_year_value": "current",
        "当年值": "current", "本年值": "current", "当年收入": "current", "current_year": "current",
        "previous": "previous", "previous_value": "previous", "previous_year_value": "previous",
        "previous_year": "previous", "上年值": "previous", "去年值": "previous", "上年收入": "previous",
    }
    if subject:
        aliases.update({f"当年{subject}": "current", f"本年{subject}": "current", f"上年{subject}": "previous", f"去年{subject}": "previous"})
    try:
        tree = ast.parse(text, mode="eval")
        if len(text) > 1000 or sum(1 for _ in ast.walk(tree)) > 64:
            return None
        def coefficients(node):
            if isinstance(node, ast.Name):
                return (1, 0) if aliases[node.id] == "current" else (0, 1)
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
                a, b = coefficients(node.operand)
                sign = -1 if isinstance(node.op, ast.USub) else 1
                return sign*a, sign*b
            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Sub, ast.Add)):
                a, b = coefficients(node.left); c, d = coefficients(node.right)
                sign = -1 if isinstance(node.op, ast.Sub) else 1
                return a+sign*c, b+sign*d
            raise ValueError("unsupported numerator")
        body = tree.body
        if not isinstance(body, ast.BinOp) or not isinstance(body.op, ast.Div) or coefficients(body.left) != (1, -1):
            return None
        denominator = body.right
        absolute = isinstance(denominator, ast.Call)
        if absolute:
            if not isinstance(denominator.func, ast.Name) or denominator.func.id.lower() not in {"abs", "绝对值"} or len(denominator.args) != 1 or denominator.keywords:
                return None
            denominator = denominator.args[0]
        if isinstance(denominator, ast.Name) and aliases.get(denominator.id) == "previous":
            return absolute
    except (SyntaxError, KeyError, ValueError, RecursionError):
        pass
    return None


def economic_intent(summary: str, subject: str) -> dict | None:
    """Recognise the supported economic workflow by clauses, not one sentence."""
    body = re.sub(r"\s+", "", unicodedata.normalize("NFKC", summary))
    clauses = [part for part in re.split(r"[,;。]", body) if part]
    if len(clauses) != 4 or clauses[-1] not in {"结果保留正负号", "保留结果正负号", "结果保留正负", "保留正负号"}:
        return None
    first = clauses[0]
    for word in (f"评估年度{subject}", f"当年{subject}", f"本年{subject}", "评估年度值", "本年值", "当年值"):
        first = first.replace(word, "原值")
    first = first.replace("同比增长率", "增幅").replace("同比增幅", "增幅").replace("增长率", "增幅").replace(f"{subject}增幅", "增幅")
    rank = r"(?:排名|排序)"
    connector = r"(?:并|并且|然后|后再|再)?"
    score = r"(?:赋分|给分|评分)"
    direction = r"(?:按)?(降序|升序|从大到小|从小到大|从高到低|从低到高)?"
    match = re.fullmatch(r"(?:对)?原值(?:与|和|及)增幅(?:分别|各自)"+direction+r"(?:"+rank+r")?"+connector+score, first)
    if not match:
        return None
    average = re.fullmatch(r"(?:取)?(?:平均|平均分|平均得分|均值)(?:后|之后)?(?:再次|重新|再)"+r"(?:降序)?"+rank+connector+score, clauses[1])
    growth = re.fullmatch(r"增幅(?:按|为|=)(.+?)(?:计算)?", clauses[2])
    if not average or not growth:
        return None
    absolute = growth_formula(growth[1], subject)
    if absolute is None:
        return None
    return {"ranking_direction": "ascending" if match[1] in {"升序", "从小到大", "从低到高"} else "descending",
            "growth_denominator_uses_absolute_value": absolute}


def average_formula(formula: str, components: tuple[tuple[str, ...], ...]) -> bool:
    """Compare means of component scores, including expanded arithmetic."""
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", formula)).replace("÷", "/").replace("×", "*").split("=", 1)[-1]
    aliases = {name: i for i, names in enumerate(components) for name in (*names, f"score{i+1}")}
    try:
        tree = ast.parse(text, mode="eval")
        if len(text) > 1000 or sum(1 for _ in ast.walk(tree)) > 64:
            return False
        body = tree.body
        if isinstance(body, ast.Call) and isinstance(body.func, ast.Name) and body.func.id.lower() in {"average", "mean", "平均"} and not body.keywords:
            args = body.args
            if len(args) == 1:
                if isinstance(args[0], ast.Name) and args[0].id in {"scores", "component_scores", "子项得分", "分项得分"}:
                    return True
                if isinstance(args[0], (ast.List, ast.Tuple)):
                    args = args[0].elts
            return len(args) == len(components) and all(isinstance(arg, ast.Name) for arg in args) and {aliases[arg.id] for arg in args} == set(range(len(components)))
        def linear(node):
            if isinstance(node, ast.Name):
                return 0., {aliases[node.id]: 1.}
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
                return float(node.value), {}
            if isinstance(node, ast.BinOp):
                a, b = linear(node.left); c, d = linear(node.right)
                if isinstance(node.op, (ast.Add, ast.Sub)):
                    sign = -1 if isinstance(node.op, ast.Sub) else 1
                    return a+sign*c, {key:b.get(key,0)+sign*d.get(key,0) for key in b.keys()|d.keys()}
                if isinstance(node.op, ast.Div) and not d and c != 0:
                    return a/c, {key:value/c for key,value in b.items()}
                if isinstance(node.op, ast.Mult) and (not b or not d):
                    return a*c, {key:c*b.get(key,0)+a*d.get(key,0) for key in b.keys()|d.keys()}
            raise ValueError("not a mean")
        constant, weights = linear(body)
        return constant == 0 and set(weights) == set(range(len(components))) and all(math.isclose(value, 1/len(components)) for value in weights.values())
    except (SyntaxError, KeyError, ValueError, OverflowError, ZeroDivisionError, RecursionError):
        return False


def linear_score_formula(formula: str, first: float, decrement: float) -> bool:
    """Compare arithmetic meaning; parse an affine polynomial, never execute code."""
    if len(formula) > 1000:
        return False
    formula = unicodedata.normalize("NFKC", formula).replace("×", "*").replace("÷", "/")
    formula = re.sub(r"\s+", "", formula).removeprefix("score=").removeprefix("得分=")
    for label in ("排名", "名次"):
        formula = formula.replace(label, "rank")
    names = {"first_score": first, "max_score": first, "首名分": first,
             "rank_decrement": decrement, "decrement": decrement, "递减值": decrement}
    try:
        tree = ast.parse(formula, mode="eval")
        if sum(1 for _ in ast.walk(tree)) > 64:
            return False

        def affine(node):
            if isinstance(node, ast.Constant) and isinstance(node.value, (float, int)) and not isinstance(node.value, bool):
                return float(node.value), 0.0
            if isinstance(node, ast.Name):
                if node.id == "rank":
                    return 0.0, 1.0
                return float(names[node.id]), 0.0
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
                value, slope = affine(node.operand)
                sign = -1 if isinstance(node.op, ast.USub) else 1
                return sign * value, sign * slope
            if isinstance(node, ast.BinOp):
                a, b = affine(node.left); c, d = affine(node.right)
                if isinstance(node.op, ast.Add): return a+c, b+d
                if isinstance(node.op, ast.Sub): return a-c, b-d
                if isinstance(node.op, ast.Mult) and b*d == 0: return a*c, a*d+b*c
                if isinstance(node.op, ast.Div) and d == 0 and c != 0: return a/c, b/c
            raise ValueError("not affine")

        constant, slope = affine(tree.body)
        return math.isfinite(constant) and math.isfinite(slope) and math.isclose(constant, first+decrement, abs_tol=1e-9) and math.isclose(slope, -decrement, abs_tol=1e-9)
    except (SyntaxError, ValueError, TypeError, KeyError, OverflowError, RecursionError, ZeroDivisionError):
        return False
