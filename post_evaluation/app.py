from __future__ import annotations

from io import BytesIO
import hashlib
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse, Response, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from openpyxl import load_workbook
from pydantic import BaseModel, Field

from .constants import (
    METRICS,
    PRODUCTS,
    ROLES,
    adapter_for_metrics,
    calculation_contracts,
    display_specs_for_dimension,
    input_specs_for_dimension,
)
from .exports import make_report, make_result_tables, make_workbook
from .report_analysis import generate_evaluation_draft, report_evidence, validate_draft
from .gemini_rules import GeminiNotConfigured, GeminiRuleError, parse_rule_summary, parse_weight_goal, parser_status
from .ingest import auto_mapping, detect_metrics, inspect_workbook
from .model_mapping import match_model_workbook
from .ai_mapping import mapping_with_fallback
from .model_engine import compare, simulate
from .model_engine import validate_definition
from .calculation_types import calculation_types, compile_rule, local_rule_binding, parsed_rule_binding, rule_readiness, source_metrics
from .semantic_rules import CONTRACT
from . import rule_cache
from .result_state import describe_result
from .weight_optimizer import optimize_weights
from .scoring import calculate
from . import store
from . import report_jobs
from .report_preview import report_html


app = FastAPI(title="产品后评估", version="0.1.0")


@app.get("/api/health")
def health():
    """Check local database access without calling the model provider."""
    with store.connect() as db:
        db.execute("SELECT 1").fetchone()
    return {"status": "ok", "revision": os.environ.get("APP_REVISION", "local")}


class RunInput(BaseModel):
    year: int = Field(ge=2020, le=2100)
    name: str = Field(min_length=1, max_length=80)
    model_id: str | None = None


class ModelCreateInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=40)
    clone_from: str | None = None


class ModelUpdateInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=40)
    definition: dict[str, Any]


class PublishInput(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    version: str | None = Field(default=None, min_length=1, max_length=40)
    definition: dict[str, Any] | None = None


class SimulateInput(BaseModel):
    run_id: str
    baseline_model_id: str | None = None


class WeightOptimizeInput(BaseModel):
    run_id: str
    objective: str = Field(min_length=2, max_length=500)
    max_weight_change: float = Field(default=5, ge=1, le=20)
    max_other_rank_change: int = Field(default=3, ge=0, le=12)


class RuleParseInput(BaseModel):
    dimension_label: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=3000)
    calculation_type: str | None = None
    ranking: dict[str, Any] | None = None


class ModelCheckInput(BaseModel):
    definition: dict[str, Any]


class MappingInput(BaseModel):
    sheet: str | None
    header_row: int = Field(ge=1)
    product_col: str
    fields: dict[str, str | None]
    units: dict[str, str] = Field(default_factory=dict)


class FileTargetsInput(BaseModel):
    targets: list[str] = Field(default_factory=list)


class ChoicesInput(BaseModel):
    choices: dict[str, str]


class ProductEvaluationInput(BaseModel):
    product: str = Field(max_length=100)
    evaluation: str = Field(max_length=2500)
    recommendation: str = Field(max_length=2500)
    evidence_ids: list[str] = Field(max_length=200)
    evidence_summary: list[str] = Field(default_factory=list, max_length=200)


class EvaluationDraftInput(BaseModel):
    products: list[ProductEvaluationInput] = Field(max_length=30)
    signature: str = Field(max_length=64)
    provider: str = Field(default="gemini", max_length=30)
    model: str = Field(default="", max_length=100)
    generated_at: str = Field(default="", max_length=100)


class NotesInput(BaseModel):
    analysis: str = ""
    recommendations: str = ""
    review: str = ""
    evaluation_draft: EvaluationDraftInput | None = None


def require_run(run_id: str) -> dict[str, Any]:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "评估批次不存在")
    return ensure_mapping_contracts(run)


def data_year_for_run(run: dict[str, Any]) -> int:
    """Return the year being evaluated, which is also the source-data year."""
    return int(run.get("data_year") if run.get("data_year") is not None else run["year"])


def metric_keys_for_targets(run: dict[str, Any], targets: list[str]) -> list[str]:
    dimensions = (run.get("model") or {}).get("definition", {}).get("dimensions", [])
    by_key = {item.get("key"): source_metrics(item) for item in dimensions}
    return list(dict.fromkeys(metric for target in targets for metric in by_key.get(target, [])))


def dimensions_for_targets(run: dict[str, Any], targets: list[str]) -> list[dict[str, Any]]:
    selected = set(targets)
    dimensions = (run.get("model") or {}).get("definition", {}).get("dimensions", [])
    return [item for item in dimensions if item.get("key") in selected]


def expected_fields_for_targets(run: dict[str, Any], targets: list[str]) -> list[str]:
    fields = []
    for dimension in dimensions_for_targets(run, targets):
        fields.extend(item["key"] for item in input_specs_for_dimension(dimension, data_year_for_run(run)))
    return list(dict.fromkeys(fields))


def mapping_fields_for_targets(run: dict[str, Any], targets: list[str]) -> list[str]:
    fields = expected_fields_for_targets(run, targets)
    for dimension in dimensions_for_targets(run, targets):
        fields.extend(item["key"] for item in display_specs_for_dimension(dimension, data_year_for_run(run)))
    return list(dict.fromkeys(fields))


def decorate_mapping(run: dict[str, Any], targets: list[str], mapping: dict[str, Any]) -> dict[str, Any]:
    """Attach the model's calculation contract to one batch source mapping."""
    result = dict(mapping)
    contracts = calculation_contracts(
        (run.get("model") or {}).get("definition", {}).get("dimensions", []),
        targets,
        data_year_for_run(run),
    )
    labels = {
        item["key"]: item["label"]
        for contract in contracts
        for item in [*contract["inputs"], *contract.get("display_inputs", [])]
    }
    mapped = {key for key, column in result.get("fields", {}).items() if column}
    provided_summary = []
    for contract in contracts:
        for group in contract.get("input_groups", []):
            if mapped.intersection(group.get("keys", [])):
                provided_summary.append(group["label"])
        for group in contract.get("display_groups", []):
            if mapped.intersection(group.get("keys", [])):
                provided_summary.append(group["label"])
    result.update({
        "mapping_version": 2,
        "input_labels": labels,
        "calculation_basis": contracts,
        "provided_summary": list(dict.fromkeys(provided_summary)),
    })
    return result


def ensure_mapping_contracts(run: dict[str, Any]) -> dict[str, Any]:
    """Upgrade stored batch mappings to the current raw-input contract once."""
    if not run.get("files"):
        return run
    records = {item["id"]: item for item in store.file_records(run["id"])}
    changed = False
    for item in run["files"]:
        mapping = item.get("mapping") or {}
        targets = item.get("targets") or []
        expected = set(expected_fields_for_targets(run, targets))
        mapped_fields = set(mapping_fields_for_targets(run, targets))
        fields = set((mapping.get("fields") or {}).keys())
        legacy_self_research = any(key.startswith("self_research:") for key in fields)
        if legacy_self_research:
            record = records[item["id"]]
            upgraded = auto_mapping(record["path"], metric_keys_for_targets(run, targets), data_year_for_run(run))
            upgraded["suggested_targets"] = mapping.get("suggested_targets", targets)
            upgraded["detected_metrics"] = mapping.get("detected_metrics", metric_keys_for_targets(run, targets))
        else:
            upgraded = dict(mapping)
            upgraded_fields = dict(upgraded.get("fields") or {})
            detected = auto_mapping(records[item["id"]]["path"], metric_keys_for_targets(run, targets), data_year_for_run(run))
            for key in mapped_fields:
                upgraded_fields.setdefault(key, (detected.get("fields") or {}).get(key))
            upgraded["fields"] = {
                key: value for key, value in upgraded_fields.items()
                if not mapped_fields or key in mapped_fields
            }
        upgraded = decorate_mapping(run, targets, upgraded)
        if upgraded != mapping:
            store.update_mapping(run["id"], item["id"], upgraded, invalidate=False)
            changed = True
    return store.get_run(run["id"]) if changed else run


def targets_for_metrics(run: dict[str, Any], metric_keys: list[str]) -> list[str]:
    """Match detected source fields to enabled business dimensions.

    A file may contain the raw fields for several dimensions.  The match is
    intentionally made against the model definition, so renamed or newly
    added dimensions continue to work without exposing technical keys to the
    user.
    """
    detected = set(metric_keys)
    dimensions = (run.get("model") or {}).get("definition", {}).get("dimensions", [])
    return [
        item["key"]
        for item in dimensions
        if item.get("enabled", True) and detected.intersection(source_metrics(item))
    ]


def validate_file_targets(run: dict[str, Any], targets: Any, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(targets, list) or not all(isinstance(item, str) and item.strip() for item in targets):
        raise HTTPException(400, "请至少选择一个评分指标")
    dimensions = (run.get("model") or {}).get("definition", {}).get("dimensions", [])
    valid = {item.get("key") for item in dimensions if item.get("enabled", True)}
    target_keys = list(dict.fromkeys(item.strip() for item in targets))
    if not target_keys and not allow_empty:
        raise HTTPException(400, "请至少选择一个评分指标")
    if any(item not in valid for item in target_keys):
        raise HTTPException(400, "包含当前模型中不存在或未启用的评分指标")
    return target_keys


@app.get("/api/config")
def config():
    published = next((model for model in store.list_models() if model["status"] == "published"), None)
    summaries = {}
    if published:
        for dimension in published["definition"].get("dimensions", []):
            for metric_key in dimension.get("metric_keys", []):
                summaries[metric_key] = dimension.get("rule_summary", "")
    return {"products": PRODUCTS, "metrics": [{"key": key, "label": label, "role": role, "rule_summary": summaries.get(key, "")} for key, label, role in METRICS], "roles": ROLES, "rule_version": published["version"] if published else "—", "scope": "13条重点产品", "rule_parser": parser_status(), "calculation_types": calculation_types()}


@app.get("/api/models")
def models():
    return store.list_models()


@app.post("/api/rules/parse")
def parse_rule(body: RuleParseInput):
    try:
        label, summary = body.dimension_label.strip(), body.summary.strip()
        if not label or not summary:
            raise ValueError("请填写指标名称和业务规则")
        key = rule_cache.cache_key(label, summary, body.calculation_type, body.ranking, parser_status()["model"])
        cached = rule_cache.get(key)
        if cached:
            return {**cached, "cache_hit": True}
        try:
            local = local_rule_binding(label, summary, body.calculation_type)
        except ValueError:
            # A business name may resolve omitted subjects semantically even
            # when it is not one of the catalogue's exact display labels.
            local = None
        if local:
            type_id, rule = local
            parsed = {"rule": rule, "provider": "local", "model": "supported-types-v2", "parsed_at": store.now()}
        else:
            parsed = parse_rule_summary(label, summary, source_hint=body.calculation_type)
            type_id = parsed_rule_binding(label, summary, parsed["rule"], body.calculation_type)
        dimension = {"key": "check", "label": body.dimension_label, "metric_keys": ["check"],
                     "calculation_type": type_id, "rule_summary": summary,
                     "options": {"parsed_rule": parsed["rule"], "rule_parser": {"source_summary": summary}}}
        check = rule_readiness(dimension, body.ranking)
        if not check["executable"]:
            raise ValueError("；".join(check["errors"]))
        if "execution_plan" in parsed["rule"]:
            parsed["rule"] = compile_rule(dimension, type_id, body.ranking)["rule"]
        result = {**parsed, "calculation_type": type_id, "execution": check,
                  "execution_contract": CONTRACT if "execution_plan" in parsed["rule"] else "supported-types-v2"}
        rule_cache.put(key, result)
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except GeminiNotConfigured as exc:
        raise HTTPException(503, "这条规则还需要进一步理解，AI 辅助识别服务暂未连接；请明确原始数据、排序方向和计分方式后重试") from exc
    except GeminiRuleError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/models/validate")
def check_model(body: ModelCheckInput):
    dimensions = body.definition.get("dimensions") or []
    return {"errors": validate_definition(body.definition, require_100=True),
            "indicators": [rule_readiness(item, body.definition.get("ranking")) for item in dimensions if isinstance(item, dict)] if isinstance(dimensions, list) else []}


@app.get("/api/models/{model_id}")
def get_model(model_id: str):
    model = store.get_model(model_id)
    if not model:
        raise HTTPException(404, "模型不存在")
    return {**model, "events": store.model_events(model_id)}


@app.post("/api/models")
def create_model(body: ModelCreateInput):
    try:
        return store.create_model(body.name.strip(), body.version.strip(), body.clone_from)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/api/models/{model_id}")
def delete_model(model_id: str):
    try:
        deleted = store.delete_model(model_id)
        return {"deleted": True, **deleted}
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.put("/api/models/{model_id}")
def update_model(model_id: str, body: ModelUpdateInput):
    try:
        return store.update_model(model_id, body.name.strip(), body.version.strip(), body.definition)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/models/{model_id}/publish")
def publish_model(model_id: str, body: PublishInput | None = None):
    try:
        return store.publish_model(
            model_id,
            body.name.strip() if body and body.name is not None else None,
            body.version.strip() if body and body.version is not None else None,
            body.definition if body else None,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/models/{model_id}/simulate")
def simulate_model(model_id: str, body: SimulateInput):
    model = store.get_model(model_id)
    if not model:
        raise HTTPException(404, "模型不存在")
    run = require_run(body.run_id)
    try:
        candidate = simulate(model["definition"], run.get("result"))
        baseline = None
        if body.baseline_model_id:
            baseline_model = store.get_model(body.baseline_model_id)
            if not baseline_model:
                raise HTTPException(404, "对比模型不存在")
            baseline = simulate(baseline_model["definition"], run.get("result"))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"model": model, "baseline_model_id": body.baseline_model_id, "simulation": compare(candidate, baseline)}


@app.post("/api/models/{model_id}/optimize-weights")
def optimize_model_weights(model_id: str, body: WeightOptimizeInput):
    model = store.get_model(model_id)
    if not model:
        raise HTTPException(404, "模型不存在")
    run = require_run(body.run_id)
    if not run.get("result"):
        raise HTTPException(409, "所选批次尚未生成评分结果，请先完成数据校验和指标评分")
    try:
        interpretation = parse_weight_goal(body.objective.strip(), PRODUCTS)
        optimization = optimize_weights(
            model["definition"],
            run["result"],
            interpretation["goal"],
            max_weight_change=body.max_weight_change,
            max_other_rank_change=body.max_other_rank_change,
        )
    except GeminiNotConfigured as exc:
        raise HTTPException(503, str(exc)) from exc
    except GeminiRuleError as exc:
        raise HTTPException(502, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "model": model,
        "run_id": run["id"],
        "interpretation": interpretation,
        "optimization": optimization,
    }


@app.get("/api/runs")
def runs():
    return store.list_runs()


@app.post("/api/runs")
def create_run(body: RunInput):
    try:
        return store.create_run(body.year, body.name.strip(), body.model_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/api/runs/{run_id}")
def delete_run(run_id: str):
    try:
        deleted = store.delete_run(run_id)
        return {"deleted": True, **deleted}
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    run = require_run(run_id)
    return {**run, "result": describe_result((run.get("model") or {}).get("definition"), run["result"])} if run.get("result") else run


@app.post("/api/runs/{run_id}/files")
async def upload_files(run_id: str, files: list[UploadFile] = File(...)):
    """Batch-upload workbooks and automatically assign their likely purposes.

    Multiple files may contribute different fields to the same dimension.
    Overlapping fields are resolved later by the existing source-conflict
    validation instead of blocking the dimension association here.
    """
    run = require_run(run_id)
    if not files:
        raise HTTPException(400, "请至少选择一份 Excel 文件")
    outcomes = []
    existing = {item["sha256"]: item for item in run["files"]}
    for index, file in enumerate(files):
        outcome = {"index": index, "name": file.filename or "所选文件"}
        if not file.filename or not file.filename.lower().endswith(".xlsx"):
            outcomes.append({**outcome, "status": "failed", "code": "invalid_extension", "message": "请选择 .xlsx 文件", "retryable": False})
            continue
        try:
            content = await file.read(30 * 1024 * 1024 + 1)
        except OSError:
            outcomes.append({**outcome, "status": "failed", "code": "read_failed", "message": "读取文件失败，请重试", "retryable": True})
            continue
        if len(content) > 30 * 1024 * 1024:
            outcomes.append({**outcome, "status": "failed", "code": "too_large", "message": "文件超过 30 MB，请缩减后重新选择", "retryable": False})
            continue
        digest = hashlib.sha256(content).hexdigest()
        if digest in existing:
            matched = existing[digest]
            outcomes.append({**outcome, "status": "duplicate", "file_id": matched["id"], "existing_name": matched["name"], "message": f"相同内容已上传为“{matched['name']}”，已跳过", "retryable": False})
            continue
        try:
            load_workbook(BytesIO(content), read_only=True).close()
            inspection = inspect_workbook(BytesIO(content))
            suggested_targets, detected_metrics, mapping = match_model_workbook(BytesIO(content), (run.get("model") or {}).get("definition", {}).get("dimensions", []), data_year_for_run(run))
            suggested_targets, detected_metrics, mapping = await run_in_threadpool(
                mapping_with_fallback, BytesIO(content), file.filename,
                (run.get("model") or {}).get("definition", {}).get("dimensions", []),
                data_year_for_run(run), (suggested_targets, detected_metrics, mapping))
            target_keys = suggested_targets
            # Keep the recognition decision with the file so the UI can expose
            # a business-level suggestion without recomputing it client-side.
            mapping["suggested_targets"] = suggested_targets
            mapping["detected_metrics"] = detected_metrics
            mapping = decorate_mapping(run, target_keys, mapping)
        except Exception as exc:
            outcomes.append({**outcome, "status": "failed", "code": "invalid_workbook", "message": "无法读取工作簿，请确认文件可正常打开并另存为 .xlsx 后重新选择", "retryable": False})
            continue
        try:
            file_id = store.add_file(run_id, adapter_for_metrics(detected_metrics), target_keys, file.filename, content, mapping, inspection)
        except Exception:
            outcomes.append({**outcome, "status": "failed", "code": "save_failed", "message": "文件保存失败，请重试", "retryable": True})
            continue
        existing[digest] = {"id": file_id, "name": file.filename}
        outcomes.append({**outcome, "status": "uploaded", "file_id": file_id, "message": "上传成功", "retryable": False})
    # Preserve the Run response shape used by existing clients; detailed outcomes
    # apply to this request only and are not part of the persisted batch.
    return {**require_run(run_id), "upload_results": outcomes}


@app.delete("/api/runs/{run_id}/files/{file_id}")
def delete_file(run_id: str, file_id: str):
    require_run(run_id)
    try:
        store.delete_file(run_id, file_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return require_run(run_id)


@app.put("/api/runs/{run_id}/files/{file_id}/mapping")
def set_mapping(run_id: str, file_id: str, body: MappingInput):
    run = require_run(run_id)
    file = next((item for item in run["files"] if item["id"] == file_id), None)
    if not file:
        raise HTTPException(404, "文件不存在")
    required = set(expected_fields_for_targets(run, file.get("targets", [])))
    allowed = set(mapping_fields_for_targets(run, file.get("targets", [])))
    if not required.issubset(body.fields) or not set(body.fields).issubset(allowed):
        raise HTTPException(400, "字段映射须包含该数据类别的全部字段")
    if body.sheet and body.sheet not in {sheet["name"] for sheet in file["inspection"]}:
        raise HTTPException(400, "工作表不存在")
    store.update_mapping(run_id, file_id, {**body.model_dump(), "mapping_method": "manual", "mapping_note": "已手动确认计算依据"})
    return require_run(run_id)


@app.put("/api/runs/{run_id}/files/{file_id}/targets")
def set_file_targets(run_id: str, file_id: str, body: FileTargetsInput):
    run = require_run(run_id)
    file = next((item for item in run["files"] if item["id"] == file_id), None)
    if not file:
        raise HTTPException(404, "文件不存在")
    target_keys = validate_file_targets(run, body.targets, allow_empty=True)
    record = next(item for item in store.file_records(run_id) if item["id"] == file_id)
    metric_keys = metric_keys_for_targets(run, target_keys)
    _, _, new_mapping = match_model_workbook(record["path"], (run.get("model") or {}).get("definition", {}).get("dimensions", []), data_year_for_run(run), target_keys)
    new_mapping["suggested_targets"] = target_keys
    new_mapping["detected_metrics"] = metric_keys
    new_mapping = decorate_mapping(run, target_keys, new_mapping)
    store.update_file_targets(run_id, file_id, target_keys, new_mapping)
    return require_run(run_id)


@app.put("/api/runs/{run_id}/source-choices")
def set_choices(run_id: str, body: ChoicesInput):
    run = require_run(run_id)
    valid_ids = {file["id"] for file in run["files"]}
    if any(file_id not in valid_ids for file_id in body.choices.values()):
        raise HTTPException(400, "指定来源文件不存在")
    store.update_run_json(run_id, "source_choices", body.choices)
    return require_run(run_id)


@app.put("/api/runs/{run_id}/notes")
def set_notes(run_id: str, body: NotesInput):
    require_run(run_id)
    store.update_run_json(run_id, "notes", body.model_dump(exclude_none=True))
    return require_run(run_id)


def validate_batch(run: dict[str, Any]) -> dict[str, Any]:
    """Validate the current batch without publishing a scoring result.

    Validation deliberately uses the same source extraction and calculation
    contract as scoring, so a passed check means the Calculate action will use
    the exact data configuration that was checked.
    """
    issues: list[dict[str, Any]] = []
    if not run["files"]:
        issues.append({"level": "error", "code": "no_files", "message": "请先上传至少一份参与评分的文件"})
    elif not any(file.get("targets") for file in run["files"]):
        issues.append({"level": "error", "code": "no_targets", "message": "请至少为一份文件确认评分用途"})

    definition = (run.get("model") or {}).get("definition")
    result = calculate(data_year_for_run(run), store.file_records(run["id"]), run["source_choices"], definition)
    issues.extend(result["issues"])
    errors = [item for item in issues if item.get("level") == "error"]
    return {
        "status": "passed" if not errors else "failed",
        "validated_at": store.now(),
        "issues": issues,
        "conflicts": result["conflicts"],
        "error_count": len(errors),
        "warning_count": len([item for item in issues if item.get("level") == "warning"]),
    }


@app.post("/api/runs/{run_id}/validate")
def validate_run(run_id: str):
    run = require_run(run_id)
    try:
        validation = validate_batch(run)
    except Exception as exc:
        raise HTTPException(422, f"数据校验失败，请检查字段映射：{exc}") from exc
    store.update_validation(run_id, validation)
    return require_run(run_id)


@app.post("/api/runs/{run_id}/calculate")
def calculate_run(run_id: str):
    run = require_run(run_id)
    if (run.get("validation") or {}).get("status") != "passed":
        raise HTTPException(409, "请先完成数据校验；数据、用途或字段映射变更后需要重新校验")
    try:
        definition = (run.get("model") or {}).get("definition")
        result = calculate(data_year_for_run(run), store.file_records(run_id), run["source_choices"], definition)
    except Exception as exc:
        raise HTTPException(422, f"计算失败，请检查字段映射：{exc}") from exc
    store.update_run_json(run_id, "result", result)
    return result


@app.get("/api/runs/{run_id}/export.xlsx")
def export_workbook(run_id: str):
    run = require_run(run_id)
    if not run["result"]:
        raise HTTPException(409, "请先计算")
    result = run["result"]
    content = make_workbook(run, result)
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename=product-evaluation-{run['year']}.xlsx",
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/runs/{run_id}/result-tables")
def result_tables(run_id: str):
    run = require_run(run_id)
    if not run["result"]:
        raise HTTPException(409, "请先完成计算")
    return make_result_tables(run, run["result"])


@app.post("/api/runs/{run_id}/report-analysis")
def report_analysis(run_id: str, body: NotesInput):
    run = require_run(run_id)
    if not run["result"]:
        raise HTTPException(409, "请先计算评分结果，再生成详细评价")
    try:
        return generate_evaluation_draft({**run, "notes": body.model_dump(exclude_none=True)}, run["result"])
    except GeminiNotConfigured as exc:
        raise HTTPException(503, str(exc)) from exc
    except GeminiRuleError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/runs/{run_id}/ai-report", status_code=202)
def start_ai_report(run_id: str, body: NotesInput | None = None):
    run = require_run(run_id)
    if not run["result"]:
        raise HTTPException(409, "请先计算评分结果，再生成报告")
    notes = {key: run["notes"].get(key, "") for key in ("analysis", "recommendations", "review")}
    if body is not None:
        notes.update(body.model_dump(exclude={"evaluation_draft"}))
    try:
        return report_jobs.start(run, notes)
    except GeminiNotConfigured as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/runs/{run_id}/ai-report")
def ai_report_status(run_id: str):
    return report_jobs.status(require_run(run_id))


@app.get("/api/runs/{run_id}/report-preview", response_class=HTMLResponse)
def preview_report(run_id: str):
    run = require_run(run_id)
    if not run["report"]:
        raise HTTPException(409, "请先生成报告")
    return HTMLResponse(report_html(store.report_content(run_id)), headers={"Cache-Control": "no-store"})


@app.post("/api/runs/{run_id}/report")
def generate_report(run_id: str, body: NotesInput):
    run = require_run(run_id)
    if not run["result"]:
        raise HTTPException(409, "请先计算评分结果，再生成报告")
    notes = body.model_dump(exclude_none=True)
    if body.evaluation_draft:
        evidence = report_evidence({**run, "notes": notes}, run["result"])
        if body.evaluation_draft.signature != evidence["signature"]:
            raise HTTPException(409, "评分结果或经营说明已变化，请重新生成 AI 评价草稿，或移除草稿后生成数据评价报告")
        try:
            notes["evaluation_draft"] = validate_draft(notes["evaluation_draft"], evidence)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    content = make_report({**run, "notes": notes}, run["result"])
    store.save_report(run, notes, content)
    return require_run(run_id)


@app.get("/api/runs/{run_id}/report.docx")
def export_report(run_id: str):
    run = require_run(run_id)
    if not run["report"]:
        raise HTTPException(409, "请先点击生成报告")
    content = store.report_content(run_id)
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", headers={"Content-Disposition": f"attachment; filename=product-evaluation-report-{run['year']}.docx", "Cache-Control": "no-store"})


frontend = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if frontend.exists():
    app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
