from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from uuid import uuid4

from .model_defaults import DEFAULT_MODEL_ID, default_model_definition, split_legacy_economic_definition
from .model_engine import validate_definition


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
        CREATE TABLE IF NOT EXISTS model_versions (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, version TEXT NOT NULL,
            year INTEGER NOT NULL, scope TEXT NOT NULL, status TEXT NOT NULL,
            definition TEXT NOT NULL, change_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, published_at TEXT
        );
        CREATE TABLE IF NOT EXISTS model_events (
            id TEXT PRIMARY KEY, model_id TEXT NOT NULL REFERENCES model_versions(id) ON DELETE CASCADE,
            event_type TEXT NOT NULL, created_at TEXT NOT NULL, detail TEXT NOT NULL
        );
    """)
    if "rule_version" not in {row[1] for row in db.execute("PRAGMA table_info(runs)")}:
        db.execute("ALTER TABLE runs ADD COLUMN rule_version TEXT NOT NULL DEFAULT 'v1-2025'")
    if "model_id" not in {row[1] for row in db.execute("PRAGMA table_info(runs)")}:
        db.execute("ALTER TABLE runs ADD COLUMN model_id TEXT")
    if not db.execute("SELECT 1 FROM model_versions LIMIT 1").fetchone():
        timestamp = now()
        db.execute(
            "INSERT INTO model_versions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                DEFAULT_MODEL_ID, "2025 年重点产品评估模型", "2025-V1", 2025,
                "13条重点产品", "published",
                json.dumps(default_model_definition(), ensure_ascii=False),
                "首版模型：六个维度、总分 100 分。", timestamp, timestamp, timestamp,
            ),
        )
        db.execute(
            "INSERT INTO model_events VALUES(?,?,?,?,?)",
            (uuid4().hex, DEFAULT_MODEL_ID, "seed", timestamp, json.dumps({"reason": "系统初始化"}, ensure_ascii=False)),
        )
    for row in db.execute("SELECT id,definition FROM model_versions").fetchall():
        definition, migrated = split_legacy_economic_definition(json.loads(row["definition"]))
        if migrated:
            timestamp = now()
            db.execute(
                "UPDATE model_versions SET definition=?,updated_at=? WHERE id=?",
                (json.dumps(definition, ensure_ascii=False), timestamp, row["id"]),
            )
            db.execute(
                "INSERT INTO model_events VALUES(?,?,?,?,?)",
                (uuid4().hex, row["id"], "structure_migration", timestamp, json.dumps({"reason": "经济效益按五个最小指标拆分配置"}, ensure_ascii=False)),
            )
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


def create_run(year: int, name: str, model_id: str | None = None) -> dict:
    run_id = uuid4().hex
    timestamp = now()
    with connect() as db:
        if model_id:
            model = db.execute("SELECT id,version,status FROM model_versions WHERE id=?", (model_id,)).fetchone()
        else:
            model = db.execute("SELECT id,version,status FROM model_versions WHERE status='published' ORDER BY published_at DESC LIMIT 1").fetchone()
        if not model:
            raise ValueError("没有可用的已发布模型")
        if model["status"] != "published":
            raise ValueError("评估批次只能绑定已发布模型")
        db.execute(
            "INSERT INTO runs(id,year,name,created_at,updated_at,rule_version,model_id) VALUES(?,?,?,?,?,?,?)",
            (run_id, year, name, timestamp, timestamp, model["version"], model["id"]),
        )
    return get_run(run_id)


def list_runs() -> list[dict]:
    with connect() as db:
        rows = db.execute("SELECT id,year,name,created_at,updated_at,rule_version,model_id FROM runs ORDER BY updated_at DESC").fetchall()
    return [dict(row) for row in rows]


def delete_run(run_id: str) -> dict:
    with connect() as db:
        row = db.execute("SELECT id,name FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise LookupError("评估批次不存在")
        deleted = dict(row)
        db.execute("DELETE FROM runs WHERE id=?", (run_id,))
    upload_root = (data_root() / "uploads").resolve()
    run_directory = (upload_root / run_id).resolve()
    if run_directory.parent == upload_root and run_directory.exists():
        shutil.rmtree(run_directory, ignore_errors=True)
    return deleted


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
    run["model"] = get_model(run.get("model_id")) if run.get("model_id") else None
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


def _model_from_row(row: sqlite3.Row | None) -> dict | None:
    if not row:
        return None
    model = dict(row)
    model["definition"] = json.loads(model["definition"])
    # Legacy columns remain in SQLite for backward-compatible storage only.
    # They are not part of the user-facing model contract.
    model.pop("year", None)
    model.pop("scope", None)
    return model


def list_models() -> list[dict]:
    with connect() as db:
        rows = db.execute("SELECT * FROM model_versions ORDER BY created_at DESC").fetchall()
    return [_model_from_row(row) for row in rows]


def get_model(model_id: str | None) -> dict | None:
    if not model_id:
        return None
    with connect() as db:
        row = db.execute("SELECT * FROM model_versions WHERE id=?", (model_id,)).fetchone()
    return _model_from_row(row)


def create_model(name: str, version: str, clone_from: str | None, reason: str) -> dict:
    model_id = uuid4().hex
    timestamp = now()
    with connect() as db:
        if clone_from:
            source = db.execute("SELECT year,scope,definition FROM model_versions WHERE id=?", (clone_from,)).fetchone()
            if not source:
                raise ValueError("复制来源模型不存在")
            definition = json.loads(source["definition"])
            legacy_year = source["year"]
            legacy_scope = source["scope"]
        else:
            definition = default_model_definition()
            legacy_year = datetime.now().year
            legacy_scope = "13条重点产品"
        db.execute(
            "INSERT INTO model_versions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (model_id, name, version, legacy_year, legacy_scope, "draft", json.dumps(definition, ensure_ascii=False), reason, timestamp, timestamp, None),
        )
        db.execute(
            "INSERT INTO model_events VALUES(?,?,?,?,?)",
            (uuid4().hex, model_id, "clone" if clone_from else "create", timestamp, json.dumps({"reason": reason, "clone_from": clone_from}, ensure_ascii=False)),
        )
    return get_model(model_id)


def delete_model(model_id: str) -> dict:
    with connect() as db:
        row = db.execute("SELECT id,name,status FROM model_versions WHERE id=?", (model_id,)).fetchone()
        if not row:
            raise LookupError("模型不存在")
        if row["status"] == "published":
            raise PermissionError("已发布模型不可删除，请先发布替代版本使其归档")
        if db.execute("SELECT 1 FROM runs WHERE model_id=? LIMIT 1", (model_id,)).fetchone():
            raise PermissionError("模型已被评估批次引用，不可删除")
        deleted = dict(row)
        db.execute("DELETE FROM model_versions WHERE id=?", (model_id,))
    return deleted


def update_model(model_id: str, name: str, version: str, definition: dict, reason: str) -> dict:
    errors = validate_definition(definition)
    if errors:
        raise ValueError("；".join(errors))
    timestamp = now()
    with connect() as db:
        current = db.execute("SELECT status FROM model_versions WHERE id=?", (model_id,)).fetchone()
        if not current:
            raise LookupError("模型不存在")
        if current["status"] != "draft":
            raise PermissionError("已发布或已归档模型不可修改，请复制为新草稿")
        db.execute(
            "UPDATE model_versions SET name=?,version=?,definition=?,change_reason=?,updated_at=? WHERE id=?",
            (name, version, json.dumps(definition, ensure_ascii=False), reason, timestamp, model_id),
        )
        db.execute(
            "INSERT INTO model_events VALUES(?,?,?,?,?)",
            (uuid4().hex, model_id, "update", timestamp, json.dumps({"reason": reason}, ensure_ascii=False)),
        )
    return get_model(model_id)


def publish_model(model_id: str, reason: str) -> dict:
    timestamp = now()
    with connect() as db:
        current = db.execute("SELECT * FROM model_versions WHERE id=?", (model_id,)).fetchone()
        if not current:
            raise LookupError("模型不存在")
        if current["status"] != "draft":
            raise PermissionError("只有草稿模型可以发布")
        errors = validate_definition(json.loads(current["definition"]), require_100=True)
        if errors:
            raise ValueError("；".join(errors))
        prior = db.execute("SELECT id FROM model_versions WHERE status='published'").fetchall()
        for item in prior:
            db.execute("UPDATE model_versions SET status='archived',updated_at=? WHERE id=?", (timestamp, item["id"]))
            db.execute(
                "INSERT INTO model_events VALUES(?,?,?,?,?)",
                (uuid4().hex, item["id"], "archive", timestamp, json.dumps({"reason": f"由 {current['version']} 替代"}, ensure_ascii=False)),
            )
        db.execute("UPDATE model_versions SET status='published',published_at=?,updated_at=?,change_reason=? WHERE id=?", (timestamp, timestamp, reason, model_id))
        db.execute(
            "INSERT INTO model_events VALUES(?,?,?,?,?)",
            (uuid4().hex, model_id, "publish", timestamp, json.dumps({"reason": reason}, ensure_ascii=False)),
        )
    return get_model(model_id)


def model_events(model_id: str) -> list[dict]:
    with connect() as db:
        rows = db.execute("SELECT id,event_type,created_at,detail FROM model_events WHERE model_id=? ORDER BY created_at DESC", (model_id,)).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["detail"] = json.loads(item["detail"])
        result.append(item)
    return result
