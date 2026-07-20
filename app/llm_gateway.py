"""LLM gateway to NVIDIA NIM (OpenAI-compatible). Acts as the LiteLLM-style
abstraction from the spec: model routing (fast vs reasoning), JSON-mode helper,
embeddings, retries. Falls back to a deterministic stub if no API key is set so
the whole app stays runnable offline."""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from . import config

_TIMEOUT = httpx.Timeout(120.0, connect=15.0)


class LLMError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.NVIDIA_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def chat(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1024,
    reasoning: bool = False,
    timeout: float | None = None,
    retries: int = 3,
) -> str:
    """Return the assistant text for a chat completion.

    `reasoning=True` routes to the strong model; otherwise the fast model.
    `timeout` overrides the default read timeout (seconds).
    """
    if not config.has_api_key():
        return _offline_chat(messages)

    model = model or (config.REASONING_MODEL if reasoning else config.FAST_MODEL)
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    to = httpx.Timeout(timeout, connect=15.0) if timeout else _TIMEOUT
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            with httpx.Client(timeout=to) as client:
                r = client.post(
                    f"{config.NIM_BASE_URL}/chat/completions",
                    headers=_headers(),
                    json=payload,
                )
            if r.status_code == 200:
                content = r.json()["choices"][0]["message"].get("content")
                return content or ""  # NIM may return null content on empty completions
            last_err = LLMError(f"NIM {r.status_code}: {r.text[:300]}")
        except Exception as e:  # noqa: BLE001 - surface after retries
            last_err = e
    raise LLMError(f"chat failed after retries: {last_err}")


def chat_json(
    messages: list[dict[str, str]],
    *,
    reasoning: bool = False,
    temperature: float = 0.0,
    max_tokens: int = 1024,
    timeout: float | None = None,
    retries: int = 3,
) -> dict[str, Any]:
    """Chat call that must return a JSON object. Robust to code fences / prose."""
    sys_nudge = {
        "role": "system",
        "content": "You are a precise API. Respond with ONE valid JSON object only. "
        "No markdown, no code fences, no commentary.",
    }
    msgs = [sys_nudge, *messages]
    raw = chat(
        msgs, reasoning=reasoning, temperature=temperature,
        max_tokens=max_tokens, timeout=timeout, retries=retries,
    )
    return _extract_json(raw)


def embed(texts: list[str], *, input_type: str = "passage") -> list[list[float]]:
    """Return embedding vectors. Falls back to a hashing embedder offline."""
    if not config.has_api_key():
        return [_offline_embed(t) for t in texts]

    payload = {
        "model": config.EMBEDDING_MODEL,
        "input": texts,
        "input_type": input_type,  # "query" | "passage" for nv-embedqa
        "encoding_format": "float",
        "truncate": "END",
    }
    with httpx.Client(timeout=_TIMEOUT) as client:
        r = client.post(
            f"{config.NIM_BASE_URL}/embeddings", headers=_headers(), json=payload
        )
    if r.status_code != 200:
        raise LLMError(f"embeddings {r.status_code}: {r.text[:300]}")
    data = r.json()["data"]
    return [row["embedding"] for row in data]


# --------------------------------------------------------------------------- #
# JSON extraction
# --------------------------------------------------------------------------- #
def _extract_json(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    # strip code fences
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # grab the first balanced {...} block
    start = raw.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(raw)):
            if raw[i] == "{":
                depth += 1
            elif raw[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[start : i + 1])
                    except json.JSONDecodeError:
                        break
    raise LLMError(f"could not parse JSON from model output: {raw[:200]}")


# --------------------------------------------------------------------------- #
# Offline fallbacks (keep the app demoable without network / key)
# --------------------------------------------------------------------------- #
def _offline_chat(messages: list[dict[str, str]]) -> str:
    last = messages[-1]["content"] if messages else ""
    return (
        "[offline stub] NVIDIA_API_KEY not set. Echoing intent: "
        + last[:160]
    )


def _offline_embed(text: str) -> list[float]:
    import hashlib

    dim = config.EMBEDDING_DIM
    vec = [0.0] * dim
    for tok in re.findall(r"\w+", text.lower()):
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]
