"""One place where the pipeline talks to the LLM (Groq for now).

`extract()` sends content to the model and gets back an object validated
against a Pydantic schema. If the reply doesn't fit the schema, we ask once
for a corrected reply; if it still doesn't fit, we raise LLMUnavailable and
the caller holds the item for review. Never a half-filled object.

Three modes (env LLM_MODE):
- live   : call the model (needs GROQ_API_KEY). Replies are cached on disk
           under cache/llm/, keyed by a hash of the request, so a re-run gives
           the same answers and costs nothing.
- replay : read saved answers from LLM_FIXTURES_DIR instead of calling the model.
           Used by tests and for offline development. Never the default.
- off    : no LLM; callers treat every item as "needs review".
"""
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import TypeVar

import requests
from pydantic import BaseModel, ValidationError

from ..clients.http import send

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

URL = os.getenv("GROQ_URL", "https://api.groq.com/openai/v1/chat/completions")
MODEL = os.getenv("GROQ_MODEL") or "openai/gpt-oss-120b"
PROMPT_VERSION = "v1"   # bump when prompts change, so old cache entries are not reused


class LLMUnavailable(RuntimeError):
    """The model could not give a valid answer; the item must be held for review."""


def mode() -> str:
    """live / replay / off. Default: live if a key is set, otherwise off."""
    m = os.getenv("LLM_MODE", "").strip().lower()
    if m:
        return m
    return "live" if os.getenv("GROQ_API_KEY") else "off"


def _cache_path(kind: str, key: str) -> Path:
    """Where the reply to one request is saved."""
    return Path(os.getenv("LLM_CACHE_DIR", "cache/llm")) / kind / f"{key}.json"


def _safe(ref: str) -> str:
    """Make a reference like 'area_manager_chat-10/07/26, 9:47' safe as a file name."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in ref)


def _text(content: list[dict]) -> str:
    """Join the text blocks of the content into one user message."""
    return "\n\n".join(b["text"] for b in content if b["type"] == "text")


def _chat(messages: list[dict]) -> str:
    """POST one chat completion (retries on 429/5xx) and return the reply text."""
    key = os.getenv("GROQ_API_KEY", "")
    if not key:
        raise LLMUnavailable("GROQ_API_KEY is not set")
    body = {"model": MODEL, "messages": messages, "temperature": 0,
            "response_format": {"type": "json_object"}}
    if MODEL.startswith("openai/gpt-oss"):
        body.update(reasoning_effort="medium", include_reasoning=False)   # think, but only return the JSON
    try:
        resp = send(requests.Session(), "POST", URL, json=body, timeout=120,
                    headers={"Authorization": f"Bearer {key}"})
    except requests.RequestException as exc:
        raise LLMUnavailable(f"model unreachable: {exc}") from exc
    if resp.status_code != 200:
        raise LLMUnavailable(f"model returned {resp.status_code}: {resp.text[:300]}")
    return resp.json()["choices"][0]["message"]["content"] or ""


def _structured(system: str, content: list[dict], schema: type[T]) -> T:
    """Ask for JSON that fits `schema`; on a bad reply, ask once more with the error."""
    messages = [
        {"role": "system", "content": system + "\n\nReply with ONLY a JSON object that matches this JSON schema:\n"
                                      + json.dumps(schema.model_json_schema())},
        {"role": "user", "content": _text(content)},
    ]
    for attempt in (1, 2):
        reply = _chat(messages)
        try:
            return schema.model_validate_json(reply)
        except ValidationError as exc:
            if attempt == 2:
                raise LLMUnavailable(f"output failed validation twice: {str(exc)[:300]}") from exc
            log.info("LLM reply did not fit the schema; asking once for a fix")
            messages += [{"role": "assistant", "content": reply},
                         {"role": "user", "content": f"Your reply does not match the schema: {str(exc)[:500]}\n"
                                                     "Reply again with only the corrected JSON object."}]


def extract(kind: str, ref: str, system: str, content: list[dict], schema: type[T]) -> T:
    """Fill `schema` from `content`. `ref` names the item (e.g. the log row) for replay files and errors."""
    m = mode()
    if m == "off":
        raise LLMUnavailable("no LLM configured (set GROQ_API_KEY)")

    if m == "replay":
        path = Path(os.environ["LLM_FIXTURES_DIR"]) / kind / f"{_safe(ref)}.json"
        if not path.exists():
            raise LLMUnavailable(f"no replay fixture {path}")
        return schema.model_validate_json(path.read_text(encoding="utf-8"))

    request = {"model": MODEL, "v": PROMPT_VERSION, "system": system, "content": content,
               "schema": schema.model_json_schema()}
    key = hashlib.sha256(json.dumps(request, sort_keys=True, default=str).encode()).hexdigest()[:32]
    cached = _cache_path(kind, key)
    if cached.exists():
        return schema.model_validate_json(cached.read_text(encoding="utf-8"))

    try:
        result = _structured(system, content, schema)
    except LLMUnavailable as exc:
        raise LLMUnavailable(f"{kind} {ref}: {exc}") from exc
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result
