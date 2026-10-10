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
from .scoring import calculate
from . import store


app = FastAPI(title="产品后评估", version="0.1.0")


class RunInput(BaseModel):
    year: int = Field(ge=2020, le=2100)
    name: str = Field(min_length=1, max_length=80)


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
    return {"products": PRODUCTS, "metrics": [{"key": key, "label": label, "role": role} for key, label, role in METRICS], "roles": ROLES, "rule_version": "v1-2025", "scope": "13条重点产品"}


@app.get("/api/runs")
def runs():
    return store.list_runs()


@app.post("/api/runs")
def create_run(body: RunInput):
    return store.create_run(body.year, body.name.strip())


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
        result = calculate(run["year"], store.file_records(run_id), run["source_choices"])
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
