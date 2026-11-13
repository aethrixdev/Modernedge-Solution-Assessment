"""One place where the pipeline talks to the LLM (Claude).

`extract()` sends content to Claude and gets back an object validated against
a Pydantic schema (structured outputs: the API itself makes the reply fit the
schema). If no valid object comes back, we raise LLMUnavailable and the caller
holds the item for review. Never a half-filled object.

Three modes (env LLM_MODE):
- live   : call Claude (needs ANTHROPIC_API_KEY). Replies are cached on disk
           under cache/llm/, keyed by a hash of the request, so a re-run gives
           the same answers and costs nothing.
- replay : read saved answers from LLM_FIXTURES_DIR instead of calling the model.
           Used by tests and for offline development. Never the default.
- off    : no LLM; callers treat every item as "needs review".
"""
import hashlib
import json
import os
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

MODEL = os.getenv("LLM_MODEL") or "claude-opus-5-5"
PROMPT_VERSION = "v2"   # bump when prompts change, so old cache entries are not reused


class LLMUnavailable(RuntimeError):
    """The model could not give a valid answer; the item must be held for review."""


def mode() -> str:
    """live / replay / off. Default: live if a key is set, otherwise off."""
    m = os.getenv("LLM_MODE", "").strip().lower()
    if m:
        return m
    return "live" if os.getenv("ANTHROPIC_API_KEY") else "off"


def _cache_path(kind: str, key: str) -> Path:
    """Where the reply to one request is saved."""
    return Path(os.getenv("LLM_CACHE_DIR", "cache/llm")) / kind / f"{key}.json"


def _safe(ref: str) -> str:
    """Make a reference like 'area_manager_chat-10/07/26, 9:47' safe as a file name."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in ref)


def _ask_claude(kind: str, ref: str, system: str, content: list[dict], schema: type[T]) -> T:
    """One structured-output request to Claude; raise LLMUnavailable if no valid object comes back."""
    import anthropic  # imported here so the pipeline runs without the SDK in "off" mode

    client = anthropic.Anthropic(max_retries=4)          # the SDK retries 429/5xx itself
    try:
        response = client.beta.messages.parse(
            model=MODEL,
            max_tokens=4000,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_format=schema,
            output_config={"effort": "medium"},
            # If a safety classifier declines, the API retries on a fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.APIError as exc:
        raise LLMUnavailable(f"{kind} {ref}: {exc}") from exc
    if response.stop_reason == "refusal":
        raise LLMUnavailable(f"{kind} {ref}: model declined")
    if response.parsed_output is None:
        raise LLMUnavailable(f"{kind} {ref}: no structured output (stop_reason={response.stop_reason})")
    try:
        return schema.model_validate(response.parsed_output.model_dump())   # belt and braces
    except ValidationError as exc:
        raise LLMUnavailable(f"{kind} {ref}: output failed validation: {exc}") from exc


def extract(kind: str, ref: str, system: str, content: list[dict], schema: type[T]) -> T:
    """Fill `schema` from `content`. `ref` names the item (e.g. the log row) for replay files and errors."""
    m = mode()
    if m == "off":
        raise LLMUnavailable("no LLM configured (set ANTHROPIC_API_KEY)")

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

    result = _ask_claude(kind, ref, system, content, schema)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result
