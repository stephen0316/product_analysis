from __future__ import annotations

from collections import defaultdict
from io import BytesIO
from pathlib import Path
import re
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from .constants import PRODUCTS, expected_fields


def normalize_product(value: Any, role: str) -> str | None:
    if value is None:
        return None
    compact = re.sub(r"\s+", "", str(value)).casefold()
    aliases = {re.sub(r"\s+", "", p).casefold(): p for p in PRODUCTS}
    aliases.update({"onenet城市物联网平台": "OneNET", "onepark智慧园区": "OnePark"})
    if role == "market":
        aliases.update({"安全芯片": "芯片", "芯片（安全)": "芯片", "芯片(安全)": "芯片"})
    return aliases.get(compact)


def inspect_workbook(path: str | Path) -> list[dict[str, Any]]:
    wb = load_workbook(path, read_only=True, data_only=True)
    result = []
    for ws in wb:
        if ws.max_row < 1 or ws.max_column < 1:
            continue
        preview = []
        for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row, 7), max_col=min(ws.max_column, 25), values_only=True):
            preview.append([display_value(v) for v in row])
        result.append({"name": ws.title, "rows": ws.max_row, "columns": ws.max_column, "preview": preview})
    wb.close()
    return result


def display_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.8g}"
    return str(value)[:160]


def auto_mapping(path: str | Path, role: str, year: int) -> dict[str, Any]:
    wb = load_workbook(path, read_only=True, data_only=False)
    candidate = None
    for ws in wb:
        first = " ".join(str(ws.cell(r, c).value or "") for r in range(1, min(4, ws.max_row) + 1) for c in range(1, min(ws.max_column, 14) + 1))
        if role == "market" and "市场份额" in first and "公司产品" in first:
            candidate = ws
            mapping = {"sheet": ws.title, "header_row": 1, "product_col": "D", "fields": {f"market_share:{year}": _find_year_column(ws, 1, year, "市场份额")}}
            break
        if role == "finance" and year == 2025 and "产品收入" in first and "产品毛利" in first:
            candidate = ws
            mapping = {
                "sheet": ws.title, "header_row": 3, "product_col": "A",
                "fields": {
                    f"revenue:{year-1}": "C", f"revenue:{year}": "D",
                    f"gross_profit:{year-1}": "H", f"gross_profit:{year}": "I",
                    f"gross_margin:{year-1}": "K", f"gross_margin:{year}": "L",
                    f"operating_profit:{year-1}": "N", f"operating_profit:{year}": "O",
                    f"operating_margin:{year-1}": "P", f"operating_margin:{year}": "Q",
                },
            }
            break
        if role == "three_eff" and all(word in first for word in ("劳产", "元效", "人效")):
            candidate = ws
            header_row = next((r for r in range(1, min(ws.max_row, 5) + 1) if any("劳产" in str(ws.cell(r, c).value or "") for c in range(1, ws.max_column + 1))), 2)
            def header_col(label: str) -> str | None:
                return next((get_column_letter(c) for c in range(1, ws.max_column + 1) if label in str(ws.cell(header_row, c).value or "")), None)
            mapping = {"sheet": ws.title, "header_row": header_row, "product_col": header_col("产品线") or "B", "fields": {f"labor_output:{year}": header_col("劳产"), f"yuan_efficiency:{year}": header_col("元效"), f"human_efficiency:{year}": header_col("人效")}}
            break
    wb.close()
    if candidate is None:
        return {"sheet": None, "header_row": 1, "product_col": "A", "fields": {key: None for key in expected_fields(role, year)}}
    return mapping


def _find_year_column(ws, row: int, year: int, word: str) -> str | None:
    for c in range(1, ws.max_column + 1):
        label = str(ws.cell(row, c).value or "")
        if word in label and str(year) in label:
            return get_column_letter(c)
    return None


def extract_records(path: str | Path, file_id: str, role: str, mapping: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    issues = []
    if not mapping.get("sheet"):
        return [], [{"level": "error", "code": "mapping", "message": "尚未确认工作表与字段映射"}]
    wb = load_workbook(path, read_only=True, data_only=True)
    if mapping["sheet"] not in wb:
        wb.close()
        return [], [{"level": "error", "code": "sheet", "message": "映射的工作表不存在"}]
    ws = wb[mapping["sheet"]]
    fields = mapping.get("fields", {})
    records = []
    seen = defaultdict(list)
    try:
        product_col = _column_number(mapping.get("product_col"))
        resolved = {key: _column_number(col) for key, col in fields.items() if col}
    except ValueError as exc:
        wb.close()
        return [], [{"level": "error", "code": "column", "message": str(exc)}]
    for row in range(int(mapping.get("header_row", 1)) + 1, ws.max_row + 1):
        original = ws.cell(row, product_col).value
        product = normalize_product(original, role)
        if not product:
            continue
        for key, col in resolved.items():
            cell = ws.cell(row, col)
            value = cell.value
            if value is None or value == "":
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                issues.append({"level": "error", "code": "nonnumeric", "message": f"{product} 的 {key} 不是数值", "cell": f"{ws.title}!{cell.coordinate}"})
                continue
            record = {"product": product, "field": key, "value": float(value), "file_id": file_id, "cell": f"{ws.title}!{cell.coordinate}", "source_name": str(original), "unit": mapping.get("units", {}).get(key, "")}
            records.append(record)
            seen[(product, key)].append(record)
    wb.close()
    for (product, key), matches in seen.items():
        if len(matches) > 1:
            issues.append({"level": "error", "code": "duplicate", "message": f"同一文件中 {product} 的 {key} 出现 {len(matches)} 次", "cells": [r["cell"] for r in matches]})
    return records, issues


def _column_number(value: Any) -> int:
    from openpyxl.utils import column_index_from_string

    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z]{1,3}", value.strip()):
        return column_index_from_string(value.strip().upper())
    raise ValueError(f"无效列号：{value}")
