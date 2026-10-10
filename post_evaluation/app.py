from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openpyxl import load_workbook
from pydantic import BaseModel, Field

from .constants import METRICS, PRODUCTS, ROLES, expected_fields
from .exports import make_report, make_workbook
from .ingest import auto_mapping, inspect_workbook
from .model_engine import compare, simulate
from .scoring import calculate
from . import store


app = FastAPI(title="产品后评估", version="0.1.0")


class RunInput(BaseModel):
    year: int = Field(ge=2020, le=2100)
    name: str = Field(min_length=1, max_length=80)
    model_id: str | None = None


class ModelCreateInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=40)
    clone_from: str | None = None
    reason: str = Field(min_length=1, max_length=300)


class ModelUpdateInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=40)
    definition: dict[str, Any]
    reason: str = Field(min_length=1, max_length=300)


class PublishInput(BaseModel):
    reason: str = Field(min_length=1, max_length=300)


class SimulateInput(BaseModel):
    run_id: str
    baseline_model_id: str | None = None


class MappingInput(BaseModel):
    sheet: str | None
    header_row: int = Field(ge=1)
    product_col: str
    fields: dict[str, str | None]
    units: dict[str, str] = Field(default_factory=dict)


class ChoicesInput(BaseModel):
    choices: dict[str, str]


class NotesInput(BaseModel):
    analysis: str = ""
    recommendations: str = ""
    review: str = ""


def require_run(run_id: str) -> dict[str, Any]:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "评估批次不存在")
    return run


@app.get("/api/config")
def config():
    published = next((model for model in store.list_models() if model["status"] == "published"), None)
    summaries = {}
    if published:
        for dimension in published["definition"].get("dimensions", []):
            for metric_key in dimension.get("metric_keys", []):
                summaries[metric_key] = dimension.get("rule_summary", "")
    return {"products": PRODUCTS, "metrics": [{"key": key, "label": label, "role": role, "rule_summary": summaries.get(key, "")} for key, label, role in METRICS], "roles": ROLES, "rule_version": published["version"] if published else "—", "scope": "13条重点产品"}


@app.get("/api/models")
def models():
    return store.list_models()


@app.get("/api/models/{model_id}")
def get_model(model_id: str):
    model = store.get_model(model_id)
    if not model:
        raise HTTPException(404, "模型不存在")
    return {**model, "events": store.model_events(model_id)}


@app.post("/api/models")
def create_model(body: ModelCreateInput):
    try:
        return store.create_model(body.name.strip(), body.version.strip(), body.clone_from, body.reason.strip())
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
        return store.update_model(model_id, body.name.strip(), body.version.strip(), body.definition, body.reason.strip())
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/models/{model_id}/publish")
def publish_model(model_id: str, body: PublishInput):
    try:
        return store.publish_model(model_id, body.reason.strip())
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
    candidate = simulate(model["definition"], run.get("result"))
    baseline = None
    if body.baseline_model_id:
        baseline_model = store.get_model(body.baseline_model_id)
        if not baseline_model:
            raise HTTPException(404, "对比模型不存在")
        baseline = simulate(baseline_model["definition"], run.get("result"))
    return {"model": model, "baseline_model_id": body.baseline_model_id, "simulation": compare(candidate, baseline)}


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
    return require_run(run_id)


@app.post("/api/runs/{run_id}/files")
async def upload_file(run_id: str, role: str = Form(...), file: UploadFile = File(...)):
    run = require_run(run_id)
    if role not in ROLES:
        raise HTTPException(400, "未知数据类别")
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "仅支持 .xlsx 文件")
    content = await file.read(30 * 1024 * 1024 + 1)
    if len(content) > 30 * 1024 * 1024:
        raise HTTPException(413, "文件不能超过 30 MB")
    try:
        load_workbook(BytesIO(content), read_only=True).close()
        inspection = inspect_workbook(BytesIO(content))
        mapping = auto_mapping(BytesIO(content), role, run["year"])
    except Exception as exc:
        raise HTTPException(400, f"无法读取工作簿：{exc}") from exc
    store.add_file(run_id, role, file.filename, content, mapping, inspection)
    return require_run(run_id)


@app.put("/api/runs/{run_id}/files/{file_id}/mapping")
def set_mapping(run_id: str, file_id: str, body: MappingInput):
    run = require_run(run_id)
    file = next((item for item in run["files"] if item["id"] == file_id), None)
    if not file:
        raise HTTPException(404, "文件不存在")
    required = set(expected_fields(file["role"], run["year"]))
    if set(body.fields) != required:
        raise HTTPException(400, "字段映射须包含该数据类别的全部字段")
    if body.sheet and body.sheet not in {sheet["name"] for sheet in file["inspection"]}:
        raise HTTPException(400, "工作表不存在")
    store.update_mapping(run_id, file_id, body.model_dump())
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
    store.update_run_json(run_id, "notes", body.model_dump())
    return require_run(run_id)


@app.post("/api/runs/{run_id}/calculate")
def calculate_run(run_id: str):
    run = require_run(run_id)
    try:
        definition = (run.get("model") or {}).get("definition")
        result = calculate(run["year"], store.file_records(run_id), run["source_choices"], definition)
        model_summary = simulate(definition or {}, result) if definition else None
        if model_summary:
            result["model_summary"] = model_summary
            result["official_total_available"] = model_summary["ready"]
    except Exception as exc:
        raise HTTPException(422, f"计算失败，请检查字段映射：{exc}") from exc
    store.update_run_json(run_id, "result", result)
    return result


@app.get("/api/runs/{run_id}/export.xlsx")
def export_workbook(run_id: str):
    run = require_run(run_id)
    if not run["result"]:
        raise HTTPException(409, "请先计算")
    content = make_workbook(run, run["result"])
    return StreamingResponse(BytesIO(content), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment; filename=product-evaluation-{run['year']}.xlsx"})


@app.get("/api/runs/{run_id}/report.docx")
def export_report(run_id: str):
    run = require_run(run_id)
    if not run["result"]:
        raise HTTPException(409, "请先计算")
    content = make_report(run, run["result"])
    return StreamingResponse(BytesIO(content), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", headers={"Content-Disposition": f"attachment; filename=product-evaluation-report-{run['year']}.docx"})


frontend = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if frontend.exists():
    app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
