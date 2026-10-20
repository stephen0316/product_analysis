"""One generation per batch, with observable progress and atomic snapshot publication."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import RLock, Lock
import logging

from . import store, llm_client
from .exports import make_report
from .gemini_rules import GeminiNotConfigured, GeminiRuleError
from .report_insights import generate_report_draft

logger = logging.getLogger(__name__)
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="report")
_lock = RLock()
_render_lock = Lock()  # Matplotlib's pyplot state is process-global.
_jobs = {}


def _key(run_id):
    return (str(store.data_root().resolve()), run_id)


def status(run):
    with _lock:
        state = deepcopy(_jobs.get(_key(run["id"])))
    if state and (state["status"] in {"queued", "running", "failed"} or run.get("report")):
        return state
    return {"status": "completed" if run.get("report") else "idle", "stage": "completed" if run.get("report") else "idle"}


def start(run, notes):
    key = _key(run["id"])
    with _lock:
        existing = _jobs.get(key)
        if existing and existing["status"] in {"queued", "running"}:
            return deepcopy(existing)
        if not llm_client.configured():
            raise GeminiNotConfigured("AI 报告服务尚未配置，请配置后重试")
        _jobs[key] = {"status": "queued", "stage": "preparing", "completed": 0, "total": 0}
        _pool.submit(_generate, key, deepcopy(run), deepcopy(notes))
        return deepcopy(_jobs[key])


def _generate(key, run, notes):
    def progress(stage, completed=0, total=0):
        with _lock:
            _jobs[key] = {"status": "running", "stage": stage, "completed": completed, "total": total}
    try:
        progress("preparing")
        fingerprint = store.report_fingerprint(run)
        draft = generate_report_draft({**run, "notes": notes}, run["result"], progress)
        progress("rendering")
        notes = {**notes, "evaluation_draft": draft}
        with _render_lock:
            content = make_report({**run, "notes": notes}, run["result"])
        store.save_report(run, notes, content, expected_fingerprint=fingerprint)
        with _lock:
            _jobs[key] = {"status": "completed", "stage": "completed"}
    except Exception as exc:
        # Never send provider response bodies, secrets or business payloads to the browser.
        message = str(exc) if isinstance(exc, (GeminiNotConfigured, GeminiRuleError, ValueError)) else "报告生成失败，请重试；已有报告未更改"
        logger.warning("Report generation failed: type=%s", type(exc).__name__)
        with _lock:
            _jobs[key] = {"status": "failed", "stage": "failed", "message": message}
