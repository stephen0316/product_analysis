from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4


def data_root() -> Path:
    root = Path(os.environ.get("PRODUCT_EVAL_DATA_DIR", Path(__file__).resolve().parent.parent / "local_data"))
    root.mkdir(parents=True, exist_ok=True)
    return root


@contextmanager
def connect():
    db = sqlite3.connect(data_root() / "evaluations.sqlite3")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY, year INTEGER NOT NULL, name TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            rule_version TEXT NOT NULL DEFAULT 'v1-2025',
            source_choices TEXT NOT NULL DEFAULT '{}', notes TEXT NOT NULL DEFAULT '{}',
            result TEXT
        );
        CREATE TABLE IF NOT EXISTS files (
            id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            role TEXT NOT NULL, name TEXT NOT NULL, sha256 TEXT NOT NULL,
            path TEXT NOT NULL, mapping TEXT NOT NULL, inspection TEXT NOT NULL,
            uploaded_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS review_events (
            id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL, notes TEXT NOT NULL
        );
    """)
    if "rule_version" not in {row[1] for row in db.execute("PRAGMA table_info(runs)")}:
        db.execute("ALTER TABLE runs ADD COLUMN rule_version TEXT NOT NULL DEFAULT 'v1-2025'")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def create_run(year: int, name: str) -> dict:
    run_id = uuid4().hex
    timestamp = now()
    with connect() as db:
        db.execute("INSERT INTO runs(id,year,name,created_at,updated_at,rule_version) VALUES(?,?,?,?,?,?)", (run_id, year, name, timestamp, timestamp, "v1-2025"))
    return get_run(run_id)


def list_runs() -> list[dict]:
    with connect() as db:
        rows = db.execute("SELECT id,year,name,created_at,updated_at,rule_version FROM runs ORDER BY updated_at DESC").fetchall()
    return [dict(row) for row in rows]


def get_run(run_id: str) -> dict | None:
    with connect() as db:
        row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            return None
        files = db.execute("SELECT * FROM files WHERE run_id=? ORDER BY uploaded_at", (run_id,)).fetchall()
        review_events = db.execute("SELECT id,created_at,notes FROM review_events WHERE run_id=? ORDER BY created_at", (run_id,)).fetchall()
    run = dict(row)
    for key in ("source_choices", "notes", "result"):
        run[key] = json.loads(run[key]) if run[key] else (None if key == "result" else {})
    run["files"] = []
    run["review_history"] = [{"id": event["id"], "created_at": event["created_at"], "notes": json.loads(event["notes"])} for event in review_events]
    for file in files:
        item = dict(file)
        item["mapping"] = json.loads(item["mapping"])
        item["inspection"] = json.loads(item["inspection"])
        item.pop("path")
        run["files"].append(item)
    return run


def file_records(run_id: str) -> list[dict]:
    with connect() as db:
        rows = db.execute("SELECT * FROM files WHERE run_id=? ORDER BY uploaded_at", (run_id,)).fetchall()
    return [{**dict(row), "mapping": json.loads(row["mapping"])} for row in rows]


def add_file(run_id: str, role: str, name: str, content: bytes, mapping: dict, inspection: list) -> str:
    file_id = uuid4().hex
    directory = data_root() / "uploads" / run_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{file_id}.xlsx"
    path.write_bytes(content)
    with connect() as db:
        db.execute("INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?)", (file_id, run_id, role, name, hashlib.sha256(content).hexdigest(), str(path), json.dumps(mapping, ensure_ascii=False), json.dumps(inspection, ensure_ascii=False), now()))
        db.execute("UPDATE runs SET updated_at=?, result=NULL WHERE id=?", (now(), run_id))
    return file_id


def update_mapping(run_id: str, file_id: str, mapping: dict) -> bool:
    with connect() as db:
        cursor = db.execute("UPDATE files SET mapping=? WHERE run_id=? AND id=?", (json.dumps(mapping, ensure_ascii=False), run_id, file_id))
        if cursor.rowcount:
            db.execute("UPDATE runs SET updated_at=?, result=NULL WHERE id=?", (now(), run_id))
    return bool(cursor.rowcount)


def update_run_json(run_id: str, key: str, value: dict) -> bool:
    if key not in ("source_choices", "notes", "result"):
        raise ValueError("不可更新的字段")
    with connect() as db:
        cursor = db.execute(f"UPDATE runs SET {key}=?,updated_at=? WHERE id=?", (json.dumps(value, ensure_ascii=False), now(), run_id))
        if key == "notes" and cursor.rowcount:
            db.execute("INSERT INTO review_events VALUES(?,?,?,?)", (uuid4().hex, run_id, now(), json.dumps(value, ensure_ascii=False)))
        if key == "source_choices" and cursor.rowcount:
            db.execute("UPDATE runs SET result=NULL WHERE id=?", (run_id,))
    return bool(cursor.rowcount)
