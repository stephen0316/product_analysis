from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from uuid import uuid4

from .model_defaults import DEFAULT_MODEL_ID, default_model_definition, ensure_economic_growth_formula, ensure_indicator_categories, split_legacy_economic_definition
from .constants import ECONOMIC_FIELDS
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
            id TEXT PRIMARY KEY, year INTEGER NOT NULL, data_year INTEGER, name TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            rule_version TEXT NOT NULL DEFAULT 'v1-2025',
            source_choices TEXT NOT NULL DEFAULT '{}', notes TEXT NOT NULL DEFAULT '{}',
            result TEXT, validation TEXT
        );
        CREATE TABLE IF NOT EXISTS files (
            id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            role TEXT NOT NULL, name TEXT NOT NULL, sha256 TEXT NOT NULL,
            path TEXT NOT NULL, mapping TEXT NOT NULL, inspection TEXT NOT NULL,
            uploaded_at TEXT NOT NULL, targets TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS review_events (
            id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL, notes TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reports (
            run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
            generated_at TEXT NOT NULL, fingerprint TEXT NOT NULL, content BLOB NOT NULL
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
        CREATE TABLE IF NOT EXISTS system_meta (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
    """)
    if "rule_version" not in {row[1] for row in db.execute("PRAGMA table_info(runs)")}:
        db.execute("ALTER TABLE runs ADD COLUMN rule_version TEXT NOT NULL DEFAULT 'v1-2025'")
    if "model_id" not in {row[1] for row in db.execute("PRAGMA table_info(runs)")}:
        db.execute("ALTER TABLE runs ADD COLUMN model_id TEXT")
    if "data_year" not in {row[1] for row in db.execute("PRAGMA table_info(runs)")}:
        db.execute("ALTER TABLE runs ADD COLUMN data_year INTEGER")
        # Historical batches already used ``year`` as the evaluated data year.
        db.execute("UPDATE runs SET data_year=year WHERE data_year IS NULL")
    if "validation" not in {row[1] for row in db.execute("PRAGMA table_info(runs)")}:
        db.execute("ALTER TABLE runs ADD COLUMN validation TEXT")
    if "targets" not in {row[1] for row in db.execute("PRAGMA table_info(files)")}:
        db.execute("ALTER TABLE files ADD COLUMN targets TEXT NOT NULL DEFAULT '[]'")
    # A short-lived release stored the calendar year of execution in ``year``
    # and the evaluated year in ``data_year``. The product wording is now
    # unambiguous: evaluation year means the year being evaluated. Migrate only
    # that release's distinctive year/data_year pairing and its default name.
    if not db.execute("SELECT 1 FROM system_meta WHERE key='evaluation_year_is_data_year_v1'").fetchone():
        for row in db.execute("SELECT id,year,data_year,name FROM runs WHERE data_year=year-1").fetchall():
            old_default_name = f"{row['year']} 年重点产品后评估"
            corrected_name = f"{row['data_year']} 年重点产品后评估" if row["name"] == old_default_name else row["name"]
            db.execute(
                "UPDATE runs SET year=?,name=?,updated_at=? WHERE id=?",
                (row["data_year"], corrected_name, now(), row["id"]),
            )
        db.execute(
            "INSERT INTO system_meta(key,value) VALUES('evaluation_year_is_data_year_v1',?)",
            (now(),),
        )
    if not db.execute("SELECT 1 FROM model_versions LIMIT 1").fetchone():
        timestamp = now()
        db.execute(
            "INSERT INTO model_versions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                DEFAULT_MODEL_ID, "2025 年重点产品评估模型", "2025-V1", 2025,
                "13条重点产品", "published",
                json.dumps(default_model_definition(), ensure_ascii=False),
                "首版模型：五类维度、十项指标、总分 100 分。", timestamp, timestamp, timestamp,
            ),
        )
        db.execute(
            "INSERT INTO model_events VALUES(?,?,?,?,?)",
            (uuid4().hex, DEFAULT_MODEL_ID, "seed", timestamp, json.dumps({"reason": "系统初始化"}, ensure_ascii=False)),
        )
    for row in db.execute("SELECT id,definition FROM model_versions").fetchall():
        definition, economic_migrated = split_legacy_economic_definition(json.loads(row["definition"]))
        definition, category_migrated = ensure_indicator_categories(definition)
        definition, growth_formula_migrated = ensure_economic_growth_formula(definition)
        if economic_migrated or category_migrated or growth_formula_migrated:
            timestamp = now()
            db.execute(
                "UPDATE model_versions SET definition=?,updated_at=? WHERE id=?",
                (json.dumps(definition, ensure_ascii=False), timestamp, row["id"]),
            )
            event_detail = (
                {"summary": "修正经济效益增幅公式：分母取绝对值，结果保留正负号"}
                if growth_formula_migrated
                else {"reason": "增加五类维度层，并将评分项调整为所属指标"}
            )
            db.execute(
                "INSERT INTO model_events VALUES(?,?,?,?,?)",
                (uuid4().hex, row["id"], "structure_migration", timestamp, json.dumps(event_detail, ensure_ascii=False)),
            )
    # Older releases automatically archived every previously published model.
    # There was no manual archive action, so those legacy rows can safely be
    # restored once when enabling concurrent published models.
    if not db.execute("SELECT 1 FROM system_meta WHERE key='multiple_published_models_v1'").fetchone():
        timestamp = now()
        archived = db.execute("SELECT id,version FROM model_versions WHERE status='archived'").fetchall()
        for item in archived:
            db.execute(
                "UPDATE model_versions SET status='published',updated_at=? WHERE id=?",
                (timestamp, item["id"]),
            )
            db.execute(
                "INSERT INTO model_events VALUES(?,?,?,?,?)",
                (
                    uuid4().hex, item["id"], "restore_publish", timestamp,
                    json.dumps({"summary": f"恢复{item['version']}为可选的已发布模型"}, ensure_ascii=False),
                ),
            )
        db.execute(
            "INSERT INTO system_meta(key,value) VALUES('multiple_published_models_v1',?)",
            (timestamp,),
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


def legacy_targets(role: str) -> list[str]:
    return {
        "market": ["market_share"],
        "finance": ECONOMIC_FIELDS,
        "three_eff": ["three_eff"],
    }.get(role, [])


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
            "INSERT INTO runs(id,year,data_year,name,created_at,updated_at,rule_version,model_id) VALUES(?,?,?,?,?,?,?,?)",
            (run_id, year, year, name, timestamp, timestamp, model["version"], model["id"]),
        )
    return get_run(run_id)


def list_runs() -> list[dict]:
    with connect() as db:
        rows = db.execute("""SELECT r.*,
            (SELECT COUNT(*) FROM files f WHERE f.run_id=r.id) AS file_count,
            p.generated_at AS report_generated_at, p.fingerprint AS report_fingerprint
            FROM runs r LEFT JOIN reports p ON p.run_id=r.id
            ORDER BY r.updated_at DESC""").fetchall()
    summaries = []
    for row in rows:
        run = dict(row)
        for key in ("source_choices", "notes", "result", "validation"):
            run[key] = json.loads(run[key]) if run[key] else None
        report_current = bool(run["result"] and run["report_fingerprint"] == report_fingerprint(run))
        summaries.append({**{key: run[key] for key in ("id", "year", "data_year", "name", "created_at", "updated_at", "rule_version", "model_id")},
                          "progress": run_progress(run, run["file_count"], report_current)})
    return summaries


def run_progress(run: dict, file_count: int, report_current: bool) -> dict:
    """Derive the next deliberate step from persisted batch evidence."""
    if run.get("result"):
        status = ("report_ready", "已生成报告", "report", "查看报告") if report_current else ("results_ready", "已有结果", "overview", "查看结果")
    elif not file_count:
        status = ("awaiting_upload", "待上传", "upload", "上传数据")
    elif (run.get("validation") or {}).get("status") == "passed":
        status = ("awaiting_calculation", "待计算", "validation", "开始计算")
    else:
        status = ("awaiting_validation", "待校验", "validation", "处理校验问题" if (run.get("validation") or {}).get("status") == "failed" else "数据校验")
    return dict(zip(("status", "label", "next_view", "action"), status), file_count=file_count)


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
        report = db.execute("SELECT generated_at,fingerprint FROM reports WHERE run_id=?", (run_id,)).fetchone()
    run = dict(row)
    for key in ("source_choices", "notes", "result", "validation"):
        run[key] = json.loads(run[key]) if run[key] else (None if key in {"result", "validation"} else {})
    run["report"] = {"generated_at": report["generated_at"]} if report and run["result"] and report["fingerprint"] == report_fingerprint(run) else None
    run["files"] = []
    run["model"] = get_model(run.get("model_id")) if run.get("model_id") else None
    run["review_history"] = [{"id": event["id"], "created_at": event["created_at"], "notes": json.loads(event["notes"])} for event in review_events]
    for file in files:
        item = dict(file)
        item["mapping"] = json.loads(item["mapping"])
        item["inspection"] = json.loads(item["inspection"])
        # An empty list is meaningful: it marks an uploaded reference file
        # whose automatic match needs a business-user decision.  Do not turn
        # it back into a formal source based on the old adapter role.
        item["targets"] = json.loads(item["targets"] or "[]")
        item.pop("path")
        run["files"].append(item)
    run["progress"] = run_progress(run, len(run["files"]), bool(run["report"]))
    return run


def file_records(run_id: str) -> list[dict]:
    with connect() as db:
        rows = db.execute("SELECT * FROM files WHERE run_id=? ORDER BY uploaded_at", (run_id,)).fetchall()
    return [{**dict(row), "mapping": json.loads(row["mapping"]), "targets": json.loads(row["targets"] or "[]")} for row in rows]


def add_file(run_id: str, role: str, targets: list[str], name: str, content: bytes, mapping: dict, inspection: list) -> str:
    file_id = uuid4().hex
    directory = data_root() / "uploads" / run_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{file_id}.xlsx"
    try:
        path.write_bytes(content)
        with connect() as db:
            db.execute("INSERT INTO files(id,run_id,role,name,sha256,path,mapping,inspection,uploaded_at,targets) VALUES(?,?,?,?,?,?,?,?,?,?)", (file_id, run_id, role, name, hashlib.sha256(content).hexdigest(), str(path), json.dumps(mapping, ensure_ascii=False), json.dumps(inspection, ensure_ascii=False), now(), json.dumps(targets, ensure_ascii=False)))
            db.execute("UPDATE runs SET updated_at=?, result=NULL, validation=NULL WHERE id=?", (now(), run_id))
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return file_id


def delete_file(run_id: str, file_id: str) -> dict:
    """Delete one stored upload and release any source choices that used it."""
    with connect() as db:
        row = db.execute("SELECT id,name,path FROM files WHERE run_id=? AND id=?", (run_id, file_id)).fetchone()
        if not row:
            raise LookupError("文件不存在")
        deleted = dict(row)
        run = db.execute("SELECT source_choices FROM runs WHERE id=?", (run_id,)).fetchone()
        choices = json.loads(run["source_choices"] or "{}") if run else {}
        choices = {field: source_id for field, source_id in choices.items() if source_id != file_id}
        db.execute("DELETE FROM files WHERE run_id=? AND id=?", (run_id, file_id))
        db.execute(
            "UPDATE runs SET source_choices=?,updated_at=?,result=NULL,validation=NULL WHERE id=?",
            (json.dumps(choices, ensure_ascii=False), now(), run_id),
        )
    upload_root = (data_root() / "uploads" / run_id).resolve()
    path = Path(deleted["path"]).resolve()
    if path.parent == upload_root and path.exists():
        path.unlink()
    return {"id": deleted["id"], "name": deleted["name"]}


def update_mapping(run_id: str, file_id: str, mapping: dict, *, invalidate: bool = True) -> bool:
    with connect() as db:
        cursor = db.execute("UPDATE files SET mapping=? WHERE run_id=? AND id=?", (json.dumps(mapping, ensure_ascii=False), run_id, file_id))
        if cursor.rowcount and invalidate:
            db.execute("UPDATE runs SET updated_at=?, result=NULL, validation=NULL WHERE id=?", (now(), run_id))
    return bool(cursor.rowcount)


def update_file_targets(run_id: str, file_id: str, targets: list[str], mapping: dict | None = None) -> bool:
    with connect() as db:
        if mapping is None:
            cursor = db.execute("UPDATE files SET targets=? WHERE run_id=? AND id=?", (json.dumps(targets, ensure_ascii=False), run_id, file_id))
        else:
            cursor = db.execute("UPDATE files SET targets=?,mapping=? WHERE run_id=? AND id=?", (json.dumps(targets, ensure_ascii=False), json.dumps(mapping, ensure_ascii=False), run_id, file_id))
        if cursor.rowcount:
            db.execute("UPDATE runs SET updated_at=?, result=NULL, validation=NULL WHERE id=?", (now(), run_id))
    return bool(cursor.rowcount)


def report_fingerprint(run: dict) -> str:
    inputs = {key: run.get(key) for key in ("year", "data_year", "name", "rule_version", "model_id", "notes", "result", "validation")}
    return hashlib.sha256(json.dumps(inputs, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def save_report(run: dict, notes: dict, content: bytes) -> None:
    snapshot = {**run, "notes": notes}
    timestamp = now()
    with connect() as db:
        db.execute("UPDATE runs SET notes=?,updated_at=? WHERE id=?", (json.dumps(notes, ensure_ascii=False), timestamp, run["id"]))
        db.execute("INSERT INTO review_events VALUES(?,?,?,?)", (uuid4().hex, run["id"], timestamp, json.dumps(notes, ensure_ascii=False)))
        db.execute("INSERT OR REPLACE INTO reports VALUES(?,?,?,?)", (run["id"], timestamp, report_fingerprint(snapshot), content))


def report_content(run_id: str) -> bytes | None:
    with connect() as db:
        row = db.execute("SELECT content FROM reports WHERE run_id=?", (run_id,)).fetchone()
    return bytes(row["content"]) if row else None


def update_run_json(run_id: str, key: str, value: dict) -> bool:
    if key not in ("source_choices", "notes", "result"):
        raise ValueError("不可更新的字段")
    with connect() as db:
        cursor = db.execute(f"UPDATE runs SET {key}=?,updated_at=? WHERE id=?", (json.dumps(value, ensure_ascii=False), now(), run_id))
        if key == "notes" and cursor.rowcount:
            db.execute("INSERT INTO review_events VALUES(?,?,?,?)", (uuid4().hex, run_id, now(), json.dumps(value, ensure_ascii=False)))
        if key == "source_choices" and cursor.rowcount:
            db.execute("UPDATE runs SET result=NULL,validation=NULL WHERE id=?", (run_id,))
    return bool(cursor.rowcount)


def update_validation(run_id: str, validation: dict) -> bool:
    with connect() as db:
        cursor = db.execute(
            "UPDATE runs SET validation=?,updated_at=? WHERE id=?",
            (json.dumps(validation, ensure_ascii=False), now(), run_id),
        )
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


def _display_number(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else str(number)


def summarize_model_changes(
    old_name: str,
    old_version: str,
    old_definition: dict,
    new_name: str,
    new_version: str,
    new_definition: dict,
) -> str:
    """Create a concise audit summary from business-facing model changes."""
    changes: list[str] = []
    if old_name != new_name:
        changes.append(f"模型名称改为{new_name}")
    if old_version != new_version:
        changes.append(f"版本号改为{new_version}")

    old_ranking = old_definition.get("ranking", {})
    new_ranking = new_definition.get("ranking", {})
    if old_ranking.get("first_score") != new_ranking.get("first_score"):
        changes.append(f"首名分调整为{_display_number(new_ranking.get('first_score'))}")
    if old_ranking.get("decrement") != new_ranking.get("decrement"):
        changes.append(f"每名递减调整为{_display_number(new_ranking.get('decrement'))}")

    old_categories = {item.get("key"): item for item in old_definition.get("categories", [])}
    new_categories = {item.get("key"): item for item in new_definition.get("categories", [])}
    for key, category in new_categories.items():
        if key not in old_categories:
            changes.append(f"增加{category.get('label', '')}维度")
        elif old_categories[key].get("label") != category.get("label"):
            changes.append(f"将{old_categories[key].get('label', '')}维度重命名为{category.get('label', '')}")

    old_indicators = {item.get("key"): item for item in old_definition.get("dimensions", [])}
    new_indicators = {item.get("key"): item for item in new_definition.get("dimensions", [])}
    migrations: dict[tuple[str, str], list[str]] = {}
    for key, indicator in new_indicators.items():
        label = indicator.get("label", "")
        if key not in old_indicators:
            changes.append(f"增加{label}指标")
            continue
        previous = old_indicators[key]
        old_label = previous.get("label", "")
        if old_label != label:
            changes.append(f"将{old_label}指标重命名为{label}")
        if previous.get("category_key") != indicator.get("category_key"):
            pair = (str(previous.get("category_key") or ""), str(indicator.get("category_key") or ""))
            migrations.setdefault(pair, []).append(label)
        if previous.get("weight") != indicator.get("weight"):
            changes.append(f"调整{label}指标权重为{_display_number(indicator.get('weight'))}")
        if previous.get("rule_summary", "") != indicator.get("rule_summary", ""):
            changes.append(f"修改{label}指标规则")
        elif previous.get("calculation_type") != indicator.get("calculation_type"):
            changes.append(f"调整{label}计算类型")

    removed_category_keys = {key for key in old_categories if key not in new_categories}
    for (old_category_key, new_category_key), labels in migrations.items():
        old_category = old_categories.get(old_category_key, {})
        new_category = new_categories.get(new_category_key, {})
        subject = labels[0] if len(labels) == 1 else f"{old_category.get('label', '')}维度下的{len(labels)}项指标"
        changes.append(f"将{subject}迁移至{new_category.get('label', '未设置')}维度")

    deleted_by_category: dict[str, list[str]] = {}
    for key, indicator in old_indicators.items():
        if key not in new_indicators:
            deleted_by_category.setdefault(str(indicator.get("category_key") or ""), []).append(indicator.get("label", ""))

    for key, category in old_categories.items():
        if key not in removed_category_keys:
            continue
        deleted_labels = deleted_by_category.pop(str(key), [])
        if deleted_labels:
            changes.append(f"删除{category.get('label', '')}维度及其{len(deleted_labels)}项指标")
        else:
            changes.append(f"删除{category.get('label', '')}维度")
    for labels in deleted_by_category.values():
        changes.extend(f"删除{label}指标" for label in labels)

    # AI parser metadata under options is intentionally ignored.
    return "，".join(changes) if changes else "保存草稿（无配置变更）"


def create_model(name: str, version: str, clone_from: str | None) -> dict:
    model_id = uuid4().hex
    timestamp = now()
    with connect() as db:
        if clone_from:
            source = db.execute("SELECT year,scope,version,definition FROM model_versions WHERE id=?", (clone_from,)).fetchone()
            if not source:
                raise ValueError("复制来源模型不存在")
            definition = json.loads(source["definition"])
            legacy_year = source["year"]
            legacy_scope = source["scope"]
        else:
            definition = default_model_definition()
            legacy_year = datetime.now().year
            legacy_scope = "13条重点产品"
        summary = f"复制{source['version']}为{version}" if clone_from else f"创建模型{version}"
        db.execute(
            "INSERT INTO model_versions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (model_id, name, version, legacy_year, legacy_scope, "draft", json.dumps(definition, ensure_ascii=False), summary, timestamp, timestamp, None),
        )
        db.execute(
            "INSERT INTO model_events VALUES(?,?,?,?,?)",
            (uuid4().hex, model_id, "clone" if clone_from else "create", timestamp, json.dumps({"summary": summary, "clone_from": clone_from}, ensure_ascii=False)),
        )
    return get_model(model_id)


def delete_model(model_id: str) -> dict:
    with connect() as db:
        row = db.execute("SELECT id,name,status FROM model_versions WHERE id=?", (model_id,)).fetchone()
        if not row:
            raise LookupError("模型不存在")
        if db.execute("SELECT 1 FROM runs WHERE model_id=? LIMIT 1", (model_id,)).fetchone():
            raise PermissionError("模型已被评估批次引用，不可删除")
        if row["status"] == "published":
            published_count = db.execute(
                "SELECT COUNT(*) FROM model_versions WHERE status='published'"
            ).fetchone()[0]
            if published_count <= 1:
                raise PermissionError("系统至少需要保留一个已发布模型")
        deleted = dict(row)
        db.execute("DELETE FROM model_versions WHERE id=?", (model_id,))
    return deleted


def update_model(model_id: str, name: str, version: str, definition: dict) -> dict:
    errors = validate_definition(definition)
    if errors:
        raise ValueError("；".join(errors))
    timestamp = now()
    with connect() as db:
        current = db.execute("SELECT name,version,status,definition FROM model_versions WHERE id=?", (model_id,)).fetchone()
        if not current:
            raise LookupError("模型不存在")
        if current["status"] != "draft":
            raise PermissionError("已发布或已归档模型不可修改，请复制为新草稿")
        summary = summarize_model_changes(
            current["name"], current["version"], json.loads(current["definition"]),
            name, version, definition,
        )
        db.execute(
            "UPDATE model_versions SET name=?,version=?,definition=?,change_reason=?,updated_at=? WHERE id=?",
            (name, version, json.dumps(definition, ensure_ascii=False), summary, timestamp, model_id),
        )
        db.execute(
            "INSERT INTO model_events VALUES(?,?,?,?,?)",
            (uuid4().hex, model_id, "update", timestamp, json.dumps({"summary": summary}, ensure_ascii=False)),
        )
    return get_model(model_id)


def publish_model(
    model_id: str,
    name: str | None = None,
    version: str | None = None,
    definition: dict | None = None,
) -> dict:
    timestamp = now()
    with connect() as db:
        current = db.execute("SELECT * FROM model_versions WHERE id=?", (model_id,)).fetchone()
        if not current:
            raise LookupError("模型不存在")
        if current["status"] != "draft":
            raise PermissionError("只有草稿模型可以发布")
        current_definition = json.loads(current["definition"])
        publish_name = name if name is not None else current["name"]
        publish_version = version if version is not None else current["version"]
        publish_definition = definition if definition is not None else current_definition
        errors = validate_definition(publish_definition, require_100=True)
        if errors:
            raise ValueError("；".join(errors))
        publish_definition = deepcopy(publish_definition)
        for indicator in publish_definition["dimensions"]:
            if "calculation_type" not in indicator and len(indicator["metric_keys"]) == 1:
                indicator["calculation_type"] = indicator["metric_keys"][0]
        change_summary = summarize_model_changes(
            current["name"], current["version"], current_definition,
            publish_name, publish_version, publish_definition,
        )
        if change_summary != "保存草稿（无配置变更）":
            db.execute(
                "INSERT INTO model_events VALUES(?,?,?,?,?)",
                (uuid4().hex, model_id, "update", timestamp, json.dumps({"summary": change_summary}, ensure_ascii=False)),
            )
        summary = f"发布并锁定模型{publish_version}"
        db.execute(
            """UPDATE model_versions
               SET name=?,version=?,definition=?,status='published',published_at=?,updated_at=?,change_reason=?
               WHERE id=?""",
            (
                publish_name, publish_version, json.dumps(publish_definition, ensure_ascii=False),
                timestamp, timestamp, summary, model_id,
            ),
        )
        db.execute(
            "INSERT INTO model_events VALUES(?,?,?,?,?)",
            (uuid4().hex, model_id, "publish", timestamp, json.dumps({"summary": summary}, ensure_ascii=False)),
        )
    return get_model(model_id)


def model_events(model_id: str) -> list[dict]:
    with connect() as db:
        rows = db.execute("SELECT id,event_type,created_at,detail FROM model_events WHERE model_id=? ORDER BY created_at DESC, rowid DESC", (model_id,)).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["detail"] = json.loads(item["detail"])
        result.append(item)
    return result
