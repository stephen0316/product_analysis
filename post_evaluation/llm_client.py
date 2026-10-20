"""公司模型底座（cmiot / OpenAI 兼容接口）共享客户端。

规则识别、文件映射、报告评价三个 AI 能力共用同一网关配置：
LLM_API_KEY、LLM_BASE_URL、LLM_MODEL；仅配置 GEMINI_API_KEY 时沿用 Google 协议。
本模块只负责传输与 JSON 解析；提示词和 JSON Schema 由调用方提供，
输出仍由本地引擎校验，不执行模型生成的公式或代码。
"""
from __future__ import annotations

import json
import os
import ssl
from urllib.request import Request, urlopen

import certifi

DEFAULT_BASE_URL = "https://onerouter.cmaiot.cn/v1"
DEFAULT_MODEL = "qwen3.7-max"
GEMINI_API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_DEFAULT_MODEL = "gemini-2.5-flash"


def _pick(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def api_key() -> str:
    return _pick("LLM_API_KEY") if provider() == "openai" else _pick("GEMINI_API_KEY")


def provider() -> str:
    selected = _pick("LLM_PROVIDER").lower()
    if selected:
        if selected not in {"gemini", "openai"}:
            raise ValueError("LLM_PROVIDER 须为 gemini 或 openai")
        return selected
    # A gateway URL/model alone must not route an existing Google key elsewhere.
    return "openai" if _pick("LLM_API_KEY") else "gemini"


def base_url() -> str:
    return _pick("LLM_BASE_URL") or DEFAULT_BASE_URL


def model() -> str:
    if provider() == "openai":
        return _pick("LLM_MODEL") or DEFAULT_MODEL
    return _pick("GEMINI_MODEL") or GEMINI_DEFAULT_MODEL


def report_model() -> str:
    name = "LLM_REPORT_MODEL" if provider() == "openai" else "GEMINI_REPORT_MODEL"
    return _pick(name) or model()


def configured() -> bool:
    return bool(api_key())


def chat_json(system: str, user: str, schema: dict | None = None, *,
              temperature: float = 0, max_tokens: int | None = None,
              timeout: int = 30, key: str | None = None, model_name: str | None = None):
    """请求网关并返回解析后的 JSON 对象。

    JSON Schema 追加在 system 消息中，用户消息保持纯数据。失败时抛出
    urllib 的 HTTPError/URLError/TimeoutError，或 ValueError（内容缺失、
    被截断或中止）与 json.JSONDecodeError（正文不是合法 JSON），由调用方
    转换为各自的业务提示。
    """
    key = key or api_key()
    if not key:
        raise ValueError("模型服务尚未配置，请先设置 LLM_API_KEY 或 GEMINI_API_KEY")
    selected_model = model_name or model()
    if provider() == "gemini":
        return _gemini_json(system, user, schema, temperature=temperature,
                            max_tokens=max_tokens, timeout=timeout, key=key, model=selected_model)
    instruction = system
    if schema is not None:
        instruction += ("\n\n输出必须严格符合以下 JSON Schema 的对象，只输出 JSON，"
                        "不要输出 Markdown 代码块或其他文字：\n"
                        + json.dumps(schema, ensure_ascii=False))
    payload: dict = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": instruction},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    request = Request(
        base_url().rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    with urlopen(request, timeout=timeout,
                 context=ssl.create_default_context(cafile=certifi.where())) as response:
        raw = json.loads(response.read().decode("utf-8"))
    choices = raw.get("choices") if isinstance(raw, dict) else None
    choice = choices[0] if isinstance(choices, list) and choices else None
    if not isinstance(choice, dict):
        raise ValueError("模型未返回有效内容")
    if choice.get("finish_reason") == "length":
        raise ValueError("输出被长度限制截断")
    if choice.get("finish_reason") not in (None, "stop", "STOP"):
        raise ValueError("输出被服务限制中止")
    message = choice.get("message")
    text = message.get("content") if isinstance(message, dict) else None
    return parse_json_object(text)


def _gemini_json(system, user, schema, *, temperature, max_tokens, timeout, key, model):
    generation = {"temperature": temperature, "responseMimeType": "application/json"}
    if schema is not None:
        generation["responseJsonSchema"] = schema
    if max_tokens:
        generation["maxOutputTokens"] = max_tokens
    payload = {"systemInstruction": {"parts": [{"text": system}]},
               "contents": [{"role": "user", "parts": [{"text": user}]}],
               "generationConfig": generation}
    request = Request(f"{GEMINI_API_ROOT}/models/{model}:generateContent",
                      data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "application/json", "x-goog-api-key": key}, method="POST")
    with urlopen(request, timeout=timeout,
                 context=ssl.create_default_context(cafile=certifi.where())) as response:
        raw = json.loads(response.read().decode("utf-8"))
    candidates = raw.get("candidates") if isinstance(raw, dict) else None
    candidate = candidates[0] if isinstance(candidates, list) and candidates else None
    if not isinstance(candidate, dict):
        raise ValueError("模型未返回有效内容")
    finish = candidate.get("finishReason")
    if finish == "MAX_TOKENS":
        raise ValueError("输出被长度限制截断")
    if finish not in (None, "STOP"):
        raise ValueError("输出被服务限制中止")
    parts = candidate.get("content", {}).get("parts", [])
    text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
    return parse_json_object(text)


def parse_json_object(text) -> dict:
    """从模型正文中提取 JSON 对象，容忍 Markdown 代码围栏。"""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("模型未返回有效内容")
    cleaned = text.strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise json.JSONDecodeError("模型正文未包含 JSON 对象", cleaned, 0)
    parsed = json.loads(cleaned[start:end + 1])
    if not isinstance(parsed, dict):
        raise json.JSONDecodeError("模型正文不是 JSON 对象", cleaned, 0)
    return parsed
