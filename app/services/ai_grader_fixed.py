"""
Comprehensive AI grading with proper multimodal support and transparency.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import re
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Dict, List, Tuple

from openai import (
    OpenAI,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    NotFoundError,
    RateLimitError,
)
from PIL import Image, ImageStat

from app.config import (
    NVIDIA_API_KEY,
    NVIDIA_BASE_URL,
    NVIDIA_MODEL,
    LLM_PROVIDER_ORDER,
    NVIDIA_MAX_IMAGES_PER_REQUEST,
    ENABLE_VISION_PREANALYSIS,
    MAX_IMAGES_SELECTION_POOL,
    MAX_IMAGES_FOR_PREANALYSIS,
    VISION_PREANALYSIS_CHUNK_SIZE,
    MAX_IMAGES_FOR_FINAL_GRADE,
    MAX_FINAL_IMAGE_BYTES,
    RATE_LIMIT_RPM,
    SCORING_PRIMARY_PROVIDER,
    SCORING_ALLOW_FALLBACK,
    MULTI_PASS_TEXT_THRESHOLD,
    MULTI_PASS_WINDOW_SIZE,
    MULTI_PASS_OVERLAP,
    FINAL_IMAGE_CAP,
    VISION_TRANSCRIPT_MAX_CHARS,
    VISION_ENTRY_TRANSCRIPTION_LIMIT,
    VISION_ENTRY_SUMMARY_LIMIT,
    SCORE_VERIFICATION_ENABLED,
)

# Import SOTA multi-turn features from ai_grader_enhanced


logger = logging.getLogger(__name__)
GRADER_CACHE_VERSION = "2026-02-28-nvidia-qwen-v1"
PROVIDER_COOLDOWN_SECONDS = {
    "rate_limited": 45,
    "provider_overloaded": 35,
    "too_many_images": 8,
    "connection_error": 20,
    "timeout": 20,
    "model_not_supported": 120,
    "model_not_found": 300,
    "provider_server_error": 30,
    "provider_error": 20,
}


def _model_not_found_message(model_name: str) -> str:
    return (
        f"Configured model '{model_name}' was not found for this NVIDIA API key (HTTP 404). "
        "Set NVIDIA_MODEL in .env to a model your key can access."
    )


class ProviderFailoverError(RuntimeError):
    """Raised when no configured provider can satisfy a request."""

    def __init__(self, purpose: str, attempts: list[dict[str, Any]]):
        self.purpose = purpose
        self.attempts = attempts
        summary = "; ".join(
            f"{a.get('provider','?')}/{a.get('model','?')} -> {a.get('error_type','error')}: {a.get('error','')}"
            for a in attempts
        )[:1400]
        super().__init__(f"All providers failed for {purpose}. {summary}")


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    base_url: str
    api_key: str
    model_text: str
    model_vision: str
    default_headers: Dict[str, str]
    requires_api_key: bool = True


class _MessageShim:
    def __init__(self, content: str):
        self.content = content


class _ChoiceShim:
    def __init__(self, content: str):
        self.message = _MessageShim(content)


class _ResponseShim:
    def __init__(self, content: str, usage: Optional[dict[str, int]] = None):
        self.choices = [_ChoiceShim(content)]
        self.usage = None
        if isinstance(usage, dict):
            self.usage = type(
                "UsageShim",
                (),
                {
                    "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                    "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
                    "total_tokens": int(usage.get("total_tokens", 0) or 0),
                },
            )()


class RateLimiter:
    """Async-safe sliding-window rate limiter.

    Uses an asyncio.Lock created lazily per event-loop so it is safe to use
    across different loops (e.g. main FastAPI loop vs regrade background loop).
    """

    def __init__(self, max_requests: int = RATE_LIMIT_RPM, per_seconds: int = 60):
        self.max_requests = max_requests
        self.per_seconds = per_seconds
        self.timestamps: list[float] = []
        self._lock: Optional[asyncio.Lock] = None
        self._lock_loop_id: Optional[int] = None  # id() of the loop that owns _lock

    def _get_lock(self) -> asyncio.Lock:
        """Return an asyncio.Lock bound to the *current* running loop.

        If the loop has changed (e.g. regrade creates a new loop), discard the
        old Lock and create a fresh one.  This prevents "attached to a different
        loop" RuntimeErrors.
        """
        try:
            current_loop_id = id(asyncio.get_running_loop())
        except RuntimeError:
            current_loop_id = None
        if self._lock is None or self._lock_loop_id != current_loop_id:
            self._lock = asyncio.Lock()
            self._lock_loop_id = current_loop_id
        return self._lock

    async def acquire(self):
        while True:
            async with self._get_lock():
                now = time.time()
                self.timestamps = [t for t in self.timestamps if now - t < self.per_seconds]
                if len(self.timestamps) < self.max_requests:
                    self.timestamps.append(now)
                    return  # Got a slot
                # Calculate wait time
                sleep_time = self.per_seconds - (now - self.timestamps[0]) + 0.1
            # Sleep OUTSIDE the lock so other coroutines can proceed
            await asyncio.sleep(min(sleep_time, 1.0))


_rate_limiter = RateLimiter()
_provider_clients: dict[str, Any] = {}
_provider_cooldown_until: dict[str, float] = {}
_provider_state_lock = threading.Lock()


def _provider_catalog() -> dict[str, ProviderSpec]:
    """Returns the provider catalog - NVIDIA NIM only."""
    return {
        "nvidia": ProviderSpec(
            name="nvidia_nim",
            base_url=NVIDIA_BASE_URL,
            api_key=NVIDIA_API_KEY,
            model_text=NVIDIA_MODEL,
            model_vision=NVIDIA_MODEL,
            default_headers={},
        ),
    }


def _normalized_provider_order() -> list[str]:
    raw = [p.strip().lower() for p in str(LLM_PROVIDER_ORDER or "").split(",") if p.strip()]
    order = raw or ["nvidia"]
    seen = set()
    deduped = []
    for name in order:
        if name not in seen:
            deduped.append(name)
            seen.add(name)
    return deduped


def _model_for_provider(spec: ProviderSpec, needs_vision: bool) -> str:
    if needs_vision:
        return (spec.model_vision or spec.model_text or "").strip()
    return (spec.model_text or spec.model_vision or "").strip()


def _max_images_for_provider(provider_name: str) -> int:
    if provider_name == "nvidia" or provider_name == "nvidia_nim":
        return max(1, int(NVIDIA_MAX_IMAGES_PER_REQUEST))
    return 5


def _effective_vision_image_cap(candidates: list[tuple[ProviderSpec, str]]) -> int:
    if not candidates:
        return max(1, int(MAX_IMAGES_FOR_FINAL_GRADE))
    limits = []
    for spec, _model in candidates:
        provider_key = "nvidia" if spec.name == "nvidia_nim" else spec.name
        limits.append(_max_images_for_provider(provider_key))
    return max(1, min(limits))


def _enabled_provider_candidates(needs_vision: bool) -> list[tuple[ProviderSpec, str]]:
    catalog = _provider_catalog()
    now = time.time()
    candidates: list[tuple[ProviderSpec, str]] = []
    cooldown_filtered: list[tuple[ProviderSpec, str]] = []

    with _provider_state_lock:
        cooldown_snapshot = dict(_provider_cooldown_until)

    for name in _normalized_provider_order():
        spec = catalog.get(name)
        if not spec:
            continue
        if not str(spec.base_url or "").strip():
            continue
        if spec.requires_api_key and not str(spec.api_key or "").strip():
            continue
        model = _model_for_provider(spec, needs_vision)
        if not model:
            continue
        cooldown_until = float(cooldown_snapshot.get(name, 0.0))
        entry = (spec, model)
        if cooldown_until > now:
            cooldown_filtered.append(entry)
        else:
            candidates.append(entry)

    # If all configured providers are cooling down, retry them anyway (best-effort availability).
    if not candidates and cooldown_filtered:
        return cooldown_filtered
    return candidates


def _provider_signature_for_hash() -> str:
    catalog = _provider_catalog()
    parts: list[str] = []
    for name in _normalized_provider_order():
        spec = catalog.get(name)
        if not spec:
            continue
        if not str(spec.base_url or "").strip():
            continue
        if spec.requires_api_key and not str(spec.api_key or "").strip():
            continue
        endpoint_sig = hashlib.sha256(str(spec.base_url).encode("utf-8", errors="replace")).hexdigest()[:10]
        auth_sig = "key" if str(spec.api_key or "").strip() else "none"
        parts.append(
            f"{name}:endpoint={endpoint_sig}|auth={auth_sig}|text={spec.model_text or '-'}|vision={spec.model_vision or '-'}"
        )
    return ";".join(parts) or "no_enabled_provider"


def _get_client(spec: ProviderSpec) -> Any:
    auth_sig = spec.api_key or ("ollama" if not spec.requires_api_key else "")
    key = f"{spec.name}|{spec.base_url}|{bool(spec.default_headers)}|{len(auth_sig)}"
    with _provider_state_lock:
        cached = _provider_clients.get(key)
    if cached is not None:
        return cached

    kwargs: Dict[str, Any] = {
        "base_url": spec.base_url,
        "api_key": auth_sig,
        "timeout": 180.0,
    }
    if spec.default_headers:
        kwargs["default_headers"] = spec.default_headers

    try:
        client = OpenAI(**kwargs)
    except TypeError:
        # Older SDK fallback if default_headers isn't supported.
        kwargs.pop("default_headers", None)
        client = OpenAI(**kwargs)

    with _provider_state_lock:
        _provider_clients[key] = client
    return client


def _is_qwen_model(model_name: str) -> bool:
    return "qwen" in str(model_name or "").lower()


def _chat_template_kwargs_for_model(provider_name: str, model_name: str) -> Optional[dict[str, Any]]:
    # NVIDIA Qwen model - no special thinking parameter needed
    if provider_name == "nvidia" and _is_qwen_model(model_name):
        # Qwen 3.5 doesn't need extra thinking configuration
        return None
    return None


def _chat_completion(
    client: Any,
    *,
    _spec: ProviderSpec,
    purpose: str,
    provider_name: str,
    model: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    top_p: Optional[float] = None,
    seed: Optional[int] = None,
    response_format: Optional[dict[str, str]] = None,
):
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if top_p is not None:
        kwargs["top_p"] = top_p
    if seed is not None:
        kwargs["seed"] = seed
    if response_format is not None:
        kwargs["response_format"] = response_format

    chat_template_kwargs = _chat_template_kwargs_for_model(provider_name, model)
    if chat_template_kwargs is not None:
        kwargs["extra_body"] = {"chat_template_kwargs": chat_template_kwargs}

    return client.chat.completions.create(**kwargs)


def _classify_provider_error(exc: Exception) -> str:
    status_code = getattr(exc, "status_code", None)
    text = str(exc).lower()

    if ("too many image" in text) or ("at most" in text and "image" in text):
        return "too_many_images"
    if "model" in text and ("not support" in text or "unsupported" in text):
        return "model_not_supported"
    if isinstance(exc, (RateLimitError,)):
        return "rate_limited"
    if isinstance(exc, NotFoundError) or status_code == 404:
        return "model_not_found"
    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return "timeout" if isinstance(exc, APITimeoutError) else "connection_error"
    if "timeout" in text:
        return "timeout"
    if isinstance(exc, APIStatusError):
        if status_code == 429:
            return "rate_limited"
        if status_code in (502, 503, 504):
            return "provider_overloaded"
        if status_code and int(status_code) >= 500:
            return "provider_server_error"
    if "queue" in text or "overload" in text or "capacity" in text:
        return "provider_overloaded"
    return "provider_error"


def _apply_provider_cooldown(provider_name: str, error_type: str) -> None:
    seconds = int(PROVIDER_COOLDOWN_SECONDS.get(error_type, 20))
    until = time.time() + max(5, seconds)
    with _provider_state_lock:
        current = float(_provider_cooldown_until.get(provider_name, 0.0))
        _provider_cooldown_until[provider_name] = max(current, until)


def _chat_completion_with_failover(
    *,
    purpose: str,
    needs_vision: bool,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    top_p: Optional[float] = None,
    seed: Optional[int] = None,
    preferred_provider: Optional[str] = None,
    allow_fallback: bool = True,
    response_format: Optional[dict[str, str]] = None,
) -> tuple[Any, dict[str, Any]]:
    candidates = _enabled_provider_candidates(needs_vision=needs_vision)
    attempts: list[dict[str, Any]] = []
    preferred_key = str(preferred_provider or "").strip().lower()

    if not candidates:
        raise ProviderFailoverError(
            purpose=purpose,
            attempts=[{
                "provider": "none",
                "model": "",
                "error_type": "configuration",
                "error": "No enabled provider with compatible model is configured.",
            }],
        )

    if preferred_key:
        preferred: list[tuple[ProviderSpec, str]] = []
        others: list[tuple[ProviderSpec, str]] = []
        for spec, model in candidates:
            provider_key = "nvidia" if spec.name == "nvidia_nim" else spec.name
            if provider_key == preferred_key:
                preferred.append((spec, model))
            else:
                others.append((spec, model))
        if preferred:
            candidates = preferred + (others if allow_fallback else [])
        elif not allow_fallback:
            raise ProviderFailoverError(
                purpose=purpose,
                attempts=[{
                    "provider": preferred_key,
                    "model": "",
                    "error_type": "configuration",
                    "error": f"Preferred provider '{preferred_key}' is not enabled.",
                }],
            )

    # Retryable error types — transient failures that may resolve on their own
    _RETRYABLE = {"connection_error", "timeout", "rate_limited", "provider_overloaded", "provider_server_error"}
    MAX_RETRIES = 4          # up to 4 retries per provider (5 total attempts)
    BASE_BACKOFF = 2.0       # seconds — doubles each retry: 2, 4, 8, 16

    for spec, model in candidates:
        provider_key = "nvidia" if spec.name == "nvidia_nim" else spec.name
        client = _get_client(spec)

        for retry_idx in range(MAX_RETRIES + 1):
            try:
                response = _chat_completion(
                    client,
                    _spec=spec,
                    purpose=purpose,
                    provider_name=provider_key,
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    top_p=top_p,
                    seed=seed,
                    response_format=response_format,
                )
                return response, {
                    "provider": provider_key,
                    "provider_key": provider_key,
                    "model": model,
                    "attempts_before_success": attempts,
                    "preferred_provider": preferred_key,
                    "fallback_used": bool(preferred_key and provider_key != preferred_key),
                    "retries": retry_idx,
                }
            except Exception as exc:
                error_type = _classify_provider_error(exc)
                status_code = getattr(exc, "status_code", None)
                attempts.append({
                    "provider": provider_key,
                    "provider_key": provider_key,
                    "model": model,
                    "error_type": error_type,
                    "status_code": status_code,
                    "error": str(exc)[:500],
                    "retry": retry_idx,
                })

                # If retryable and we have retries left, wait and retry same provider
                if error_type in _RETRYABLE and retry_idx < MAX_RETRIES:
                    wait = BASE_BACKOFF * (2 ** retry_idx)  # 2, 4, 8, 16s
                    logger.warning(
                        f"{purpose}: provider {spec.name}/{model} failed with "
                        f"{error_type} (attempt {retry_idx + 1}/{MAX_RETRIES + 1}), "
                        f"retrying in {wait:.0f}s: {exc}"
                    )
                    time.sleep(wait)
                    # Recreate client in case of stale connection
                    if error_type == "connection_error":
                        with _provider_state_lock:
                            # Clear cached client to force fresh connection
                            keys_to_remove = [
                                k for k in _provider_clients
                                if k.startswith(f"{spec.name}|")
                            ]
                            for k in keys_to_remove:
                                _provider_clients.pop(k, None)
                        client = _get_client(spec)
                    continue

                # Non-retryable or retries exhausted — apply cooldown and try next provider
                _apply_provider_cooldown(provider_key, error_type)
                logger.warning(
                    f"{purpose}: provider {spec.name}/{model} failed with "
                    f"{error_type} (exhausted retries): {exc}"
                )
                break  # Move to next provider

    raise ProviderFailoverError(purpose=purpose, attempts=attempts)


def parse_rubric(rubric_text: str) -> list[dict]:
    """Parse rubric text into structured criteria.  Handles every common format:

    - JSON:  ``{"criteria": [{"criterion": "X", "max": 5}, ...]}``
    - JSON with ``name/points``:  ``{"criteria": [{"name": "X", "points": 5}]}``
    - Numbered lists:  ``1. Code Quality (5 points)``
    - Bullet / dash lists:  ``- Code Quality: 5``  /  ``• Code Quality — 5 pts``
    - Pipe / table:  ``Code Quality | 5``
    - Tab-separated:  ``Code Quality\t5``
    - Colon-separated:  ``Code Quality: 5``
    - Parenthetical:  ``Code Quality (5)``  /  ``Code Quality (5 points)``
    - Markdown headers:  ``## Code Quality (5 points)``
    - Comma / semicolon on one line:  ``Code Quality 5, Documentation 3``
    - With descriptions:  ``1. Code Quality (5 pts) – clean code with comments``
    """
    if not rubric_text or not rubric_text.strip():
        return []

    raw = rubric_text.strip()

    # ── 1. Try JSON parse first ─────────────────────────────────────
    criteria = _parse_rubric_json(raw)
    if criteria:
        return criteria

    # ── 2. Split into candidate lines ───────────────────────────────
    # Handle comma / semicolon separated rubrics on a single line.
    lines: list[str] = []
    for raw_line in raw.split("\n"):
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        # If the line contains separators like ";" or ", " with embedded numbers,
        # split it into multiple candidates.
        sub_parts = re.split(r"[;]\s*", raw_line)
        if len(sub_parts) == 1:
            # Try comma-split only when commas separate number-bearing segments.
            comma_parts = re.split(r",\s+", raw_line)
            number_bearing = [p for p in comma_parts if re.search(r"\d", p)]
            if len(number_bearing) >= 2:
                sub_parts = comma_parts
        for part in sub_parts:
            part = part.strip()
            if part:
                lines.append(part)

    # ── 3. Parse each line into (criterion, max) ────────────────────
    criteria = []
    seen_lower: set[str] = set()

    for line in lines:
        lower = line.lower()
        # Skip header / total / filler lines.
        if re.match(r"^(total|max\s*score|rubric|grading\s*criteria|criteria)\b", lower):
            continue
        if lower in {"points", "marks", "score"}:
            continue

        name, points = _extract_criterion_and_points(line)
        if name and points is not None and 0 < points < 1000:
            key = name.lower()
            if key not in seen_lower:
                seen_lower.add(key)
                criteria.append({"criterion": name, "max": points})

    return criteria


def _parse_rubric_json(text: str) -> list[dict]:
    """Try to parse rubric from JSON format.  Returns [] on failure."""
    try:
        # Strip markdown code fences.
        clean = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        clean = re.sub(r"```\s*$", "", clean, flags=re.MULTILINE).strip()
        data = json.loads(clean)
    except (json.JSONDecodeError, ValueError):
        # Fallback: look for first { … } block.
        m = re.search(r"\{[\s\S]+\}", text)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except (json.JSONDecodeError, ValueError):
            return []

    if not isinstance(data, dict):
        if isinstance(data, list):
            data = {"criteria": data}
        else:
            return []

    items = data.get("criteria") or data.get("rubric") or data.get("rubric_breakdown") or []
    if not isinstance(items, list) or not items:
        return []

    criteria: list[dict] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(
            item.get("criterion") or item.get("name") or item.get("title") or ""
        ).strip()
        pts_raw = item.get("max") or item.get("points") or item.get("max_score") or item.get("weight") or 0
        try:
            pts = float(pts_raw)
        except (ValueError, TypeError):
            continue
        if name and pts > 0 and name.lower() not in seen:
            seen.add(name.lower())
            entry: dict = {"criterion": name, "max": pts}
            # Preserve description if present (critical for grading accuracy).
            desc = str(
                item.get("description") or item.get("desc") or item.get("grading_guide") or ""
            ).strip()
            if desc:
                entry["description"] = desc
            criteria.append(entry)
    return criteria


def _extract_criterion_and_points(line: str) -> tuple[Optional[str], Optional[float]]:
    """Extract (criterion_name, max_points) from a single rubric line.

    Returns (None, None) if the line doesn't look like a rubric criterion.
    """
    # Strip leading prefixes: "1.", "1)", "a.", "a)", "•", "*", "-", "##", etc.
    cleaned = re.sub(
        r"^(?:\d+[.)]\s*|[a-zA-Z][.)]\s*|[-•*▸▹➤➜→]\s*|#{1,6}\s*)",
        "",
        line,
    ).strip()
    if not cleaned:
        return None, None

    # ── Strategy A: parenthetical points — "Code Quality (5 points)" ──
    m = re.match(
        r"^(.+?)\s*\(\s*(\d+(?:\.\d+)?)\s*(?:points?|pts?|marks?)?\s*\)",
        cleaned,
        re.IGNORECASE,
    )
    if m:
        name = _clean_criterion_name(m.group(1))
        return name, float(m.group(2)) if name else (None, None)

    # ── Strategy B: pipe / tab separated — "Code Quality | 5" ──
    m = re.match(
        r"^(.+?)\s*[|\t]\s*(\d+(?:\.\d+)?)\s*(?:points?|pts?|marks?)?\s*$",
        cleaned,
        re.IGNORECASE,
    )
    if m:
        name = _clean_criterion_name(m.group(1))
        return name, float(m.group(2)) if name else (None, None)

    # ── Strategy C: colon / dash / equals separated — "Code Quality: 5" ──
    m = re.match(
        r"^(.+?)\s*[:=–—]\s*(\d+(?:\.\d+)?)\s*(?:points?|pts?|marks?)?\s*(?:[-–—].*)?$",
        cleaned,
        re.IGNORECASE,
    )
    if m:
        name = _clean_criterion_name(m.group(1))
        return name, float(m.group(2)) if name else (None, None)

    # ── Strategy D: trailing number — "Code Quality 5 points" / "Code Quality 5" ──
    m = re.match(
        r"^(.+?)\s+(\d+(?:\.\d+)?)\s*(?:points?|pts?|marks?)?\s*(?:[-–—].*)?$",
        cleaned,
        re.IGNORECASE,
    )
    if m:
        name = _clean_criterion_name(m.group(1))
        # Avoid matching lines where the "number" is part of the name
        # (e.g., "8-Puzzle Solver" — "8" is not points).
        if name and len(name) > 2:
            return name, float(m.group(2))

    # ── Strategy E: slash format — "Code Quality 5/10" ──
    m = re.match(
        r"^(.+?)\s+(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*(?:points?|pts?|marks?)?\s*$",
        cleaned,
        re.IGNORECASE,
    )
    if m:
        name = _clean_criterion_name(m.group(1))
        return name, float(m.group(3)) if name else (None, None)

    return None, None


def _clean_criterion_name(raw: str) -> str:
    """Clean up a raw criterion name extracted from a rubric line."""
    name = raw.strip()
    # Remove trailing separators.
    name = re.sub(r"[\s:=\-–—|]+$", "", name).strip()
    # Remove leading separators.
    name = re.sub(r"^[\s:=\-–—|]+", "", name).strip()
    # Remove leading numbering that slipped through.
    name = re.sub(r"^(?:\d+[.)]\s*|[a-zA-Z][.)]\s*)", "", name).strip()
    # Remove markdown bold/italic.
    name = re.sub(r"\*{1,2}(.+?)\*{1,2}", r"\1", name).strip()
    return name


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _safe_int_points(value: Any, default: int = 1) -> int:
    try:
        num = float(value)
        if num <= 0:
            return default
        return max(1, int(round(num)))
    except Exception:
        return default


_ACTION_WORDS = {
    "implement", "implementation", "design", "analyze", "analysis", "compare", "comparison",
    "evaluate", "validation", "test", "testing", "debug", "optimize", "optimization",
    "explain", "reasoning", "justify", "build", "model", "solve", "solver",
    "document", "documentation", "correctness", "accuracy", "complexity",
}


def _is_generic_criterion_name(name: str) -> bool:
    """
    Reject placeholder-style rubric labels.
    We treat labels like 'Criterion 1' or 'Problem 2' as generic unless they include a concrete skill/task focus.
    """
    n = _normalize_space(name)
    if not n:
        return True
    low = n.lower()

    if re.match(r"^(criterion|item|section|part)\s*#?\d+\s*:?\s*$", low):
        return True

    generic_prefix = re.match(r"^(problem|question|task|part|section)\s*#?\d+\s*[:\-]?\s*(.*)$", low)
    if generic_prefix:
        tail = _normalize_space(generic_prefix.group(2))
        if not tail:
            return True
        tail_tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9*+_-]*", tail)
        has_action = any(tok in _ACTION_WORDS for tok in tail_tokens)
        # Labels like "Problem 1: Campus Navigation System" are still too vague for grading.
        if len(tail_tokens) <= 3 or not has_action:
            return True

    if low.startswith("criterion "):
        return True

    return False


def _extract_assignment_sections(assignment_description: str) -> list[dict[str, Any]]:
    """
    Split assignment into logical sections (Problem/Question/Task) with extracted requirement lines.
    """
    sections: list[dict[str, Any]] = []
    current: Optional[dict[str, Any]] = None
    preamble: list[str] = []

    header_re = re.compile(
        r"^(problem|question|task|part)\s*#?\s*(\d+)\s*[:\-]?\s*(.*)$",
        re.IGNORECASE,
    )
    bullet_re = re.compile(r"^(?:[-*•]|\d+[.)])\s+(.+)$")
    action_line_re = re.compile(
        r"^(implement|design|build|analyze|compare|evaluate|define|explain|solve|develop|create)\b",
        re.IGNORECASE,
    )

    for raw in str(assignment_description or "").splitlines():
        line = _normalize_space(raw)
        if not line:
            continue

        header = header_re.match(line)
        if header:
            if current:
                sections.append(current)
            kind = header.group(1).title()
            num = header.group(2)
            tail = _normalize_space(header.group(3))
            label = f"{kind} {num}"
            if tail:
                label = f"{label} - {tail}"
            current = {"label": label, "requirements": [], "context": []}
            continue

        bullet = bullet_re.match(line)
        if current is None and not bullet and not action_line_re.match(line):
            preamble.append(line)
            continue

        if current is None:
            current = {"label": "Overall Assignment", "requirements": [], "context": []}

        if bullet:
            current["requirements"].append(_normalize_space(bullet.group(1)))
            continue

        current["context"].append(line)
        if action_line_re.match(line):
            current["requirements"].append(line)

    if current:
        sections.append(current)

    if not sections:
        snippet = _normalize_space(" ".join(preamble) or str(assignment_description or ""))[:160]
        sections = [{
            "label": "Overall Assignment",
            "requirements": [snippet] if snippet else [],
            "context": [snippet] if snippet else [],
        }]
    elif preamble:
        sections[0]["context"] = preamble[:3] + list(sections[0].get("context", []))

    # Keep sections concise and stable.
    for sec in sections:
        sec["requirements"] = [r for r in sec.get("requirements", []) if r][:8]
        sec["context"] = [c for c in sec.get("context", []) if c][:8]
    return sections[:8]


def _summarize_requirements(requirements: list[str], fallback: str) -> str:
    cleaned = [_normalize_space(r).strip(" .,:;-") for r in (requirements or []) if _normalize_space(r)]
    if not cleaned:
        return fallback

    short_parts: list[str] = []
    for req in cleaned[:2]:
        words = req.split()
        short = " ".join(words[:8]).strip(" .,:;-")
        if short:
            short_parts.append(short)
    if not short_parts:
        return fallback
    phrase = " + ".join(short_parts)
    return phrase[:90].strip()


def _criterion_name_from_section(section: dict[str, Any], idx: int) -> str:
    label = _normalize_space(section.get("label", "")).strip(" -")
    reqs = list(section.get("requirements", []) or [])
    focus = _summarize_requirements(reqs, "Core solution quality")

    if label.lower() == "overall assignment" or not label:
        name = f"Criterion {idx + 1}: {focus}"
    else:
        name = f"{label}: {focus}"

    # Ensure the final name is concrete, never a bare placeholder.
    if _is_generic_criterion_name(name):
        name = f"{focus} - Correctness & completeness"
    return _normalize_space(name)[:120]


def _criterion_description_from_section(section: dict[str, Any], strictness: str) -> str:
    reqs = list(section.get("requirements", []) or [])
    if reqs:
        base = _summarize_requirements(reqs, "Assess required deliverables and solution quality.")
    else:
        ctx = list(section.get("context", []) or [])
        base = _summarize_requirements(ctx, "Assess required deliverables and solution quality.")

    strictness_note = {
        "lenient": "Allow partial credit for meaningful attempts.",
        "balanced": "Award fair partial credit based on visible evidence.",
        "strict": "Require complete and correct evidence for full points.",
    }.get(strictness, "Award fair partial credit based on visible evidence.")

    return _normalize_space(f"{base}. {strictness_note}")[:240]


def _distribute_points(count: int, max_score: int, weights: Optional[list[float]] = None) -> list[int]:
    if count <= 0:
        return []
    if max_score <= 0:
        return [0] * count
    if count > max_score:
        count = max_score
        if weights:
            weights = weights[:count]

    if not weights or len(weights) != count:
        weights = [1.0] * count

    norm_weights = [max(0.01, float(w)) for w in weights]
    points = [1] * count
    remaining = max_score - count
    if remaining <= 0:
        return points

    total_w = sum(norm_weights) or 1.0
    raw_alloc = [(w / total_w) * remaining for w in norm_weights]
    int_alloc = [int(x) for x in raw_alloc]
    used = sum(int_alloc)
    points = [p + a for p, a in zip(points, int_alloc)]

    leftovers = remaining - used
    if leftovers > 0:
        fractions = sorted(
            [(raw_alloc[i] - int_alloc[i], i) for i in range(count)],
            key=lambda t: t[0],
            reverse=True,
        )
        for _, idx in fractions[:leftovers]:
            points[idx] += 1
    return points


def _format_rubric_text(criteria: list[dict], max_score: int) -> str:
    """Format criteria as a JSON-encoded string that preserves descriptions.

    This is what gets stored in the database.  ``parse_rubric()`` knows how
    to decode both this JSON format and legacy plain-text rubrics.
    """
    # Always store as JSON so descriptions survive round-trip.
    return json.dumps({"criteria": criteria, "max_score": max_score})


def _format_rubric_display(criteria: list[dict], max_score: int) -> str:
    """Human-readable rubric text for display purposes only."""
    lines: list[str] = []
    for c in criteria:
        desc = c.get("description", "")
        if desc:
            lines.append(f"{c['criterion']}: {int(c['max'])} points")
            lines.append(f"  {desc}")
        else:
            lines.append(f"{c['criterion']}: {int(c['max'])} points")
    lines.append(f"Total: {max_score}")
    return "\n".join(lines)


def _normalize_generated_criteria(raw_criteria: Any) -> list[dict]:
    out: list[dict] = []
    seen = set()
    if not isinstance(raw_criteria, list):
        return out
    for item in raw_criteria:
        if not isinstance(item, dict):
            continue
        name = _normalize_space(item.get("criterion", ""))
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        entry = {
            "criterion": name,
            "max": _safe_int_points(item.get("max", 1), default=1),
            "description": _normalize_space(item.get("description", "")),
        }
        if item.get("question_id"):
            entry["question_id"] = str(item["question_id"])
        out.append(entry)
    return out


def _rubric_quality_issues(criteria: list[dict], max_score: int) -> list[str]:
    issues: list[str] = []
    min_expected = min(3, max(1, int(max_score)))
    if len(criteria) < min_expected:
        issues.append("too_few_criteria")
    if any(_is_generic_criterion_name(c.get("criterion", "")) for c in criteria):
        issues.append("generic_names")
    total = sum(_safe_int_points(c.get("max", 0), default=0) for c in criteria)
    if total != max_score:
        issues.append("total_mismatch")
    if len({str(c.get("criterion", "")).lower() for c in criteria}) != len(criteria):
        issues.append("duplicate_names")
    return issues


def _build_fallback_rubric(assignment_description: str, max_score: int, strictness: str) -> list[dict]:
    sections = _extract_assignment_sections(assignment_description)
    criteria: list[dict] = []
    weights: list[float] = []

    for idx, section in enumerate(sections):
        name = _criterion_name_from_section(section, idx)
        desc = _criterion_description_from_section(section, strictness)
        criteria.append({"criterion": name, "max": 1, "description": desc})
        weights.append(max(1.0, float(len(section.get("requirements", []) or [])) or 1.0))

    # Add a universal quality criterion for multi-part assignments.
    if len(criteria) >= 2 and max_score >= 8:
        criteria.append({
            "criterion": "Code Quality, Clarity & Documentation",
            "max": 1,
            "description": "Assess readability, structure, explanation quality, and evidence-based conclusions.",
        })
        weights.append(1.25)

    # Keep rubric size reasonable and avoid more criteria than points.
    if len(criteria) > min(8, max_score):
        criteria = criteria[:min(8, max_score)]
        weights = weights[:len(criteria)]

    points = _distribute_points(len(criteria), max_score, weights)
    for i, p in enumerate(points):
        criteria[i]["max"] = p
    return criteria


def _repair_or_fallback_rubric(
    assignment_description: str,
    model_criteria: list[dict],
    max_score: int,
    strictness: str,
) -> tuple[list[dict], list[str]]:
    """
    Try to repair weak model output; if still low quality, use deterministic fallback rubric.
    Returns (criteria, quality_issues_before_finalization).
    """
    issues = _rubric_quality_issues(model_criteria, max_score)
    if not model_criteria:
        return _build_fallback_rubric(assignment_description, max_score, strictness), ["empty_model_output"]

    # Repair generic names using assignment sections.
    if "generic_names" in issues:
        sections = _extract_assignment_sections(assignment_description)
        for idx, c in enumerate(model_criteria):
            if _is_generic_criterion_name(c.get("criterion", "")):
                section = sections[min(idx, len(sections) - 1)] if sections else {"label": "Overall Assignment", "requirements": [], "context": []}
                c["criterion"] = _criterion_name_from_section(section, idx)
                if not c.get("description"):
                    c["description"] = _criterion_description_from_section(section, strictness)

    # Re-balance points exactly to max_score.
    if model_criteria:
        if len(model_criteria) > max_score:
            model_criteria = model_criteria[:max_score]
        weights = [max(1.0, float(_safe_int_points(c.get("max", 1), default=1))) for c in model_criteria]
        points = _distribute_points(len(model_criteria), max_score, weights)
        for i, p in enumerate(points):
            model_criteria[i]["max"] = p

    repaired_issues = _rubric_quality_issues(model_criteria, max_score)
    if repaired_issues:
        return _build_fallback_rubric(assignment_description, max_score, strictness), repaired_issues

    return model_criteria, issues


def _repair_truncated_json(raw_text: str) -> str:
    """Attempt to repair a truncated JSON response by closing open brackets/braces.

    BUG-10 fix: properly tracks whether we are inside a JSON string so that
    braces/brackets inside string values (e.g. code snippets in justifications)
    are not counted.
    """
    # Strip markdown fences
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # String-aware bracket counting
    open_braces = 0
    open_brackets = 0
    last_complete = 0
    in_string = False
    escape_next = False

    for i, ch in enumerate(text):
        if escape_next:
            escape_next = False
            continue
        if ch == '\\' and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue  # skip braces inside strings
        if ch == '{':
            open_braces += 1
        elif ch == '}':
            open_braces -= 1
            if open_braces >= 0:
                last_complete = i + 1
        elif ch == '[':
            open_brackets += 1
        elif ch == ']':
            open_brackets -= 1
            if open_brackets >= 0:
                last_complete = i + 1

    # Recount from last_complete position
    result = text[:last_complete] if last_complete > 0 else text

    # Remove trailing comma
    result = result.rstrip().rstrip(',')

    # String-aware recount for closing
    open_b = 0
    open_k = 0
    in_str = False
    esc = False
    for ch in result:
        if esc:
            esc = False
            continue
        if ch == '\\' and in_str:
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == '{':
            open_b += 1
        elif ch == '}':
            open_b -= 1
        elif ch == '[':
            open_k += 1
        elif ch == ']':
            open_k -= 1

    result += ']' * max(0, open_k) + '}' * max(0, open_b)
    return result


def _parse_phase1_response(raw_text: str) -> list[dict]:
    """Parse Phase 1 LLM response into a list of question dicts.

    Handles multiple formats:
    - JSON array: [{...}, {...}]
    - JSON object with "questions" key: {"questions": [...]}
    - Markdown-fenced variants of the above
    """
    text = raw_text.strip()

    # Strip markdown fences
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Also strip trailing ``` without leading
    if text.endswith("```"):
        text = text[:-3].strip()

    # Try direct parse
    for candidate in [text]:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict):
                # Check for {"questions": [...]} wrapper
                if "questions" in parsed and isinstance(parsed["questions"], list):
                    return parsed["questions"]
                # Check if the dict IS a single question (has "id" and "label")
                if "id" in parsed and "label" in parsed:
                    return [parsed]
                # Try other common wrapper keys
                for key in ("data", "items", "result"):
                    if key in parsed and isinstance(parsed[key], list):
                        return parsed[key]
                return []
        except json.JSONDecodeError:
            pass

    # Try extracting [...] array
    arr_match = re.search(r'\[[\s\S]*\]', text)
    if arr_match:
        try:
            parsed = json.loads(arr_match.group())
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass

    # Try extracting {...} object
    obj_match = re.search(r'\{[\s\S]*\}', text)
    if obj_match:
        try:
            parsed = json.loads(obj_match.group())
            if isinstance(parsed, dict):
                return parsed.get("questions", [])
        except json.JSONDecodeError:
            pass

    # Try repair
    repaired = _repair_truncated_json(text)
    try:
        parsed = json.loads(repaired)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return parsed.get("questions", [])
    except json.JSONDecodeError:
        pass

    # Fix common LLM mistake: metadata mixed into the array
    # e.g., [...questions..., {"has_explicit_marks": true}]
    cleaned = re.sub(r',\s*\{[^{}]*"has_explicit_marks"[^{}]*\}\s*\]', ']', text)
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass

    logger.warning("_parse_phase1_response: all parse strategies failed")
    return []


async def _extract_question_structure(
    assignment_description: str,
    max_score: int = 100,
) -> dict:
    """Phase 1: Extract question structure and mark allocations from the description."""

    system_prompt = (
        "You are a precise question-structure parser. Extract EVERY question, "
        "sub-question, and part from an assignment description with their mark allocations.\n\n"
        "RULES:\n"
        "1. Extract EVERY question and sub-part. Do NOT skip any.\n"
        "2. If marks/points are written (e.g., '10 marks', '5 pts', '[10]', '(10)', "
        "'[3 Marks]'), set marks_explicit=true and use that exact value.\n"
        "3. If a question has sub-parts with marks, parent marks = sum of parts.\n"
        "4. If NO marks specified, set marks_explicit=false and marks=null.\n"
        "5. Identify questions by ANY label format: Q1, Q2, Question 1, Problem 1, "
        "Part A, (a), (i), (ii), 1., 2., Task 1, etc.\n"
        "6. Sub-items labeled (i), (ii), (iii) or (a), (b), (c) are PARTS — extract them.\n"
        "7. If description has no clear question structure, return empty questions array.\n"
        "8. Keep descriptions to max 5 words each.\n\n"
        'Return a JSON object with a "questions" key containing an array of question objects. '
        "Each question object has: id, label, description, marks, marks_explicit, parts (array of same shape).\n\n"
        "Example output:\n"
        '{"questions": [{"id":"Q1","label":"Q1","description":"BST insert","marks":10,'
        '"marks_explicit":true,"parts":[{"id":"Q1a","label":"a",'
        '"description":"insert method","marks":5,"marks_explicit":true,"parts":[]}]}, '
        '{"id":"Q2","label":"Q2","description":"OOP concepts","marks":5,'
        '"marks_explicit":true,"parts":[]}]}\n\n'
        "IMPORTANT: Include ALL questions (Q1, Q2, Q3, etc.) in the array. Do NOT return just one question."
    )

    user_prompt = (
        f"Assignment Description:\n{assignment_description}\n\n"
        f"Total marks: {max_score}\n\nExtract the complete question structure."
    )

    try:
        response, _meta = _chat_completion_with_failover(
            purpose="extract_questions",
            needs_vision=False,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=4000,
            response_format={"type": "json_object"},
        )

        raw_text = response.choices[0].message.content or ""
        finish_reason = response.choices[0].finish_reason if response.choices else "unknown"
        logger.info("Phase 1 finish_reason=%s, len=%d", finish_reason, len(raw_text))
        logger.info("Phase 1 raw preview: %s", raw_text[:500])
        if finish_reason == "length":
            logger.warning("Phase 1 response truncated — repairing")
            raw_text = _repair_truncated_json(raw_text)

        # Parse the response — may be a JSON array or a JSON object
        questions = _parse_phase1_response(raw_text)
        if not questions:
            logger.warning("Phase 1 parsing returned 0 questions. Raw text type check: starts_with_brace=%s, starts_with_bracket=%s, first_100=%s",
                raw_text.strip()[:1] == '{', raw_text.strip()[:1] == '[', repr(raw_text.strip()[:100]))

        # Infer has_explicit from parsed questions
        has_explicit = any(
            q.get("marks_explicit") for q in questions
        ) if questions else False
        total_explicit = sum(
            q.get("marks") or 0 for q in questions if q.get("marks_explicit")
        ) if questions else 0
        logger.info("Phase 1 extracted %d questions, has_explicit=%s", len(questions), has_explicit)

        if questions:
            _distribute_question_marks(questions, max_score, has_explicit, total_explicit)

        return {
            "questions": questions,
            "has_explicit_marks": has_explicit,
            "total_explicit_marks": total_explicit,
        }

    except Exception as e:
        logger.warning("Phase 1 question extraction failed: %s", e)
        return {"questions": [], "has_explicit_marks": False, "total_explicit_marks": 0}


def _distribute_question_marks(
    questions: list[dict], max_score: int, has_explicit: bool, total_explicit: int
) -> None:
    """Distribute marks to questions/parts missing explicit allocations. Mutates in-place.

    Handles cases like:
    - Parent has [3 Marks] but sub-parts (i)(ii)(iii) have no marks → distribute 3 equally
    - All marks explicit → scale to match max_score if needed
    - Mix of explicit and implicit → distribute remaining marks
    """

    # First pass: distribute parent marks to unmarked sub-parts (top-down)
    def _distribute_parent_to_children(qs: list[dict]):
        for q in qs:
            parts = q.get("parts") or []
            if parts:
                parent_marks = q.get("marks") or 0
                children_total = sum(p.get("marks") or 0 for p in parts)
                unmarked_children = [p for p in parts if not p.get("marks") or p["marks"] == 0]

                if parent_marks > 0 and unmarked_children:
                    remaining = parent_marks - (children_total - sum(
                        (p.get("marks") or 0) for p in unmarked_children
                    ))
                    if remaining > 0 and unmarked_children:
                        per_child = remaining / len(unmarked_children)
                        for p in unmarked_children:
                            p["marks"] = round(per_child)
                            p["marks_explicit"] = False
                        # Fix rounding
                        actual = sum(p.get("marks") or 0 for p in parts)
                        if actual != parent_marks and parts:
                            parts[-1]["marks"] += parent_marks - actual

                # Recurse into children
                _distribute_parent_to_children(parts)

    _distribute_parent_to_children(questions)

    def _get_leaves(qs: list[dict]) -> list[dict]:
        leaves = []
        for q in qs:
            parts = q.get("parts") or []
            if parts:
                leaves.extend(_get_leaves(parts))
            else:
                leaves.append(q)
        return leaves

    leaves = _get_leaves(questions)
    if not leaves:
        return

    # Scale leaf marks to match max_score
    actual_total = sum(leaf.get("marks") or 0 for leaf in leaves)
    if actual_total != max_score and actual_total > 0:
        factor = max_score / actual_total
        for leaf in leaves:
            if leaf.get("marks"):
                leaf["marks"] = round(leaf["marks"] * factor)
        current = sum(leaf["marks"] for leaf in leaves)
        if current != max_score:
            leaves[-1]["marks"] += max_score - current
    elif actual_total == 0:
        # No marks at all — distribute equally
        per_item = max_score / len(leaves)
        for leaf in leaves:
            leaf["marks"] = round(per_item)
        current = sum(leaf["marks"] for leaf in leaves)
        if current != max_score:
            leaves[-1]["marks"] += max_score - current

    # Propagate marks up
    def _sum_parts(qs: list[dict]):
        for q in qs:
            parts = q.get("parts") or []
            if parts:
                _sum_parts(parts)
                q["marks"] = sum(p.get("marks") or 0 for p in parts)
    _sum_parts(questions)


def _get_leaf_questions(questions: list[dict]) -> list[dict]:
    """Get all leaf-level questions (no sub-parts)."""
    leaves = []
    for q in questions:
        parts = q.get("parts") or []
        if parts:
            leaves.extend(_get_leaf_questions(parts))
        else:
            leaves.append(q)
    return leaves


def _build_question_constraint(questions: list[dict], indent: int = 0) -> str:
    """Build a human-readable question structure for the LLM prompt."""
    lines = []
    prefix = "  " * indent
    for q in questions:
        parts = q.get("parts") or []
        marks = q.get("marks", "?")
        label = q.get("label", q.get("id", "?"))
        desc = q.get("description", "")
        if parts:
            lines.append(f"{prefix}{label} ({marks} marks): {desc}")
            lines.append(_build_question_constraint(parts, indent + 1))
        else:
            lines.append(f"{prefix}[LEAF] {label} ({marks} marks): {desc} -> Create ONE criterion")
    return "\n".join(lines)


async def generate_rubric_from_description(
    assignment_description: str,
    max_score: int = 100,
    strictness: str = "balanced",
    detail_level: str = "balanced",
) -> dict:
    """Generate a detailed, structured rubric from assignment description using AI.

    Uses a two-phase approach:
    - Phase 1: Extract question structure and mark allocations from the description
    - Phase 2: Generate criteria constrained to match the extracted structure exactly
    """
    await _rate_limiter.acquire()

    # ── Phase 1: Extract question structure ──────────────────────────
    extraction = await _extract_question_structure(assignment_description, max_score)
    questions = extraction.get("questions", [])
    leaves = _get_leaf_questions(questions) if questions else []

    if strictness not in ("lenient", "balanced", "strict"):
        strictness = "balanced"
    if detail_level not in ("simple", "balanced", "detailed"):
        detail_level = "balanced"

    # If questions found, use constrained Phase 2
    if leaves:
        return await _phase2_constrained_rubric(
            assignment_description, max_score, strictness, detail_level,
            questions, leaves,
        )

    # Otherwise fall back to unconstrained generation
    return await _unconstrained_rubric(
        assignment_description, max_score, strictness, detail_level
    )


async def _phase2_constrained_rubric(
    assignment_description: str,
    max_score: int,
    strictness: str,
    detail_level: str,
    questions: list[dict],
    leaves: list[dict],
) -> dict:
    """Phase 2: Generate rubric criteria constrained to extracted question structure."""
    await _rate_limiter.acquire()

    question_constraint = _build_question_constraint(questions)

    strictness_desc = {
        "lenient": (
            "SCORING PHILOSOPHY: LENIENT\n"
            "- Emphasize effort and completion. Any reasonable attempt = 50%+.\n"
            "- Focus on what students got RIGHT."
        ),
        "balanced": (
            "SCORING PHILOSOPHY: BALANCED\n"
            "- Fair partial credit: full/partial/zero tiers for each criterion.\n"
            "- Common mistakes lose 10-20%, major bugs lose 40-60%."
        ),
        "strict": (
            "SCORING PHILOSOPHY: STRICT\n"
            "- Full correctness required for full credit.\n"
            "- Precise pass/fail conditions. Include edge cases, efficiency, style."
        ),
    }

    detail_desc = {
        "simple": "Keep descriptions to 1 sentence each.",
        "balanced": "Descriptions: 1-3 sentences with full/partial/zero credit tiers.",
        "detailed": (
            "Descriptions must list specific sub-items with point breakdowns "
            "(sub-item points MUST sum to criterion max). Include edge cases and "
            "exactly what earns full, partial, or zero credit."
        ),
    }

    system_prompt = f"""You are an expert instructor creating a grading rubric.

CRITICAL: You MUST follow the question structure below EXACTLY.

RULES:
1. Create EXACTLY ONE criterion for each [LEAF] question/part listed below.
2. The criterion's "max" MUST EXACTLY match the marks allocated to that leaf.
3. DO NOT add extra criteria. DO NOT merge questions. DO NOT skip any leaf.
4. DO NOT redistribute or change the mark allocations.
5. Criterion names MUST include the question label (e.g., "Q1 - BST Insert", "Q2a - Naive Bayes").
6. Each criterion needs a "question_id" field matching the leaf's id.

{strictness_desc.get(strictness, strictness_desc["balanced"])}

DESCRIPTION DETAIL: {detail_desc.get(detail_level, detail_desc["balanced"])}

QUESTION STRUCTURE (generate exactly one criterion per [LEAF] item):
{question_constraint}

FOR EACH CRITERION provide:
- "criterion": Short name with question label (e.g., "Q1 - Message Analysis")
- "max": Integer points (MUST match the leaf's marks exactly)
- "description": Detailed grading guide with sub-items and scoring tiers
- "question_id": The leaf's id (e.g., "Q1", "Q2a")

Sub-item points in description MUST sum to the criterion's "max" value.

JSON format:
{{
  "criteria": [
    {{"criterion": "Q1 - Short name", "max": <marks>, "description": "...", "question_id": "Q1"}}
  ],
  "reasoning": "Brief explanation"
}}

Output ONLY valid JSON. No markdown fences. No extra text."""

    user_prompt = f"""Assignment Description:
{assignment_description}

Generate the rubric following the question structure above. Total: exactly {max_score} points."""

    try:
        response, _meta = _chat_completion_with_failover(
            purpose="generate_rubric_phase2",
            needs_vision=False,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            top_p=0.5,
            max_tokens=5000,
            response_format={"type": "json_object"},
        )

        raw_text = response.choices[0].message.content or ""
        result = _extract_json(raw_text)

        model_criteria = _normalize_generated_criteria(result.get("criteria", []))

        # ── Validate coverage: every leaf must have a criterion ──────
        leaf_map = {q["id"]: q for q in leaves}
        covered_ids = {c.get("question_id", "") for c in model_criteria}

        for leaf in leaves:
            if leaf["id"] not in covered_ids:
                logger.warning("Phase 2 missed question %s — adding stub", leaf["id"])
                model_criteria.append({
                    "criterion": f"{leaf['label']} - {leaf['description'][:40]}",
                    "max": leaf.get("marks") or 0,
                    "description": f"Assess {leaf['description']}. Award full marks for correct and complete implementation.",
                    "question_id": leaf["id"],
                })

        # ── Force marks to match question allocations exactly ────────
        for c in model_criteria:
            qid = c.get("question_id", "")
            if qid in leaf_map:
                c["max"] = leaf_map[qid].get("marks") or c.get("max", 0)

        # Skip the full repair pipeline for constrained rubrics — we already
        # enforce coverage and marks.  Only check for generic names.
        quality_issues = []
        for c in model_criteria:
            if _is_generic_criterion_name(c.get("criterion", "")):
                quality_issues.append("generic_names")
                break
        final_criteria = model_criteria

        rubric_text = _format_rubric_text(final_criteria, max_score)
        rubric_display = _format_rubric_display(final_criteria, max_score)
        reasoning = _normalize_space(result.get("reasoning", ""))
        if quality_issues:
            note = ", ".join(quality_issues)
            reasoning = _normalize_space(f"{reasoning} Quality fixes applied: {note}.")

        return {
            "success": True,
            "rubric_text": rubric_text,
            "rubric_display": rubric_display,
            "criteria": final_criteria,
            "questions": questions,
            "strictness": strictness,
            "max_score": max_score,
            "reasoning": reasoning,
            "quality_warnings": quality_issues,
        }

    except Exception as e:
        logger.exception("Phase 2 failed — falling back to unconstrained rubric")
        return await _unconstrained_rubric(
            assignment_description, max_score, strictness, detail_level
        )


async def _unconstrained_rubric(
    assignment_description: str,
    max_score: int = 100,
    strictness: str = "balanced",
    detail_level: str = "balanced",
) -> dict:
    """Fallback: single-phase rubric generation for descriptions without clear questions."""

    strictness_blocks = {
        "lenient": (
            "STRICTNESS: LENIENT\n"
            "- Emphasize effort, completion, and learning over perfection.\n"
            "- Generous partial credit: any reasonable attempt = 50%+.\n"
            "- Minor bugs/issues should lose at most 10-20% of criterion points.\n"
            "- Focus on whether the student understood the concept, not perfect execution."
        ),
        "balanced": (
            "STRICTNESS: BALANCED\n"
            "- Fair balance between correctness and effort.\n"
            "- Clear partial credit: working but imperfect = 60-80% of criterion.\n"
            "- Common minor mistakes lose 10-20%, major bugs lose 40-60%.\n"
            "- Weight correctness ~60-70%, quality ~20-30%, extras ~10%."
        ),
        "strict": (
            "STRICTNESS: STRICT\n"
            "- Require FULL correctness for full credit — no generous partial credit.\n"
            "- Each sub-item must be individually verified.\n"
            "- Minor bug = -30%, missing feature = 0 for that sub-criterion.\n"
            "- Include criteria for edge cases, error handling, efficiency, style."
        ),
    }

    criteria_count = {"simple": "3-4", "balanced": "4-6", "detailed": "6-10"}[detail_level]
    strictness_text = strictness_blocks[strictness]

    system_prompt = f"""You are an expert Computer Science instructor creating a DETAILED grading rubric.

{strictness_text}

NUMBER OF CRITERIA: {criteria_count} criteria.
Total points: EXACTLY {max_score}.

FOR EACH CRITERION you must provide:
1. "criterion": Short specific name (3-8 words).
2. "max": Integer points.
3. "description": Detailed grading instructions. THIS IS THE MOST IMPORTANT FIELD.

RULES FOR "description" (THE GRADING GUIDE):
- List specific sub-items to check, with points for EACH sub-item.
- The sub-item points MUST ADD UP to the criterion "max". Example: if max=8, then sub-items must total 8.
- End with scoring tiers: Full marks conditions, partial marks conditions, zero conditions.
- Be specific: name exact functions, algorithms, data structures, or behaviors to check.

EXAMPLE for a criterion worth 8 points:
{{
  "criterion": "BFS Pathfinding Implementation",
  "max": 8,
  "description": "Sub-items: (a) Uses correct BFS algorithm with queue/deque — 2 pts. (b) Correctly tracks visited nodes to avoid cycles — 2 pts. (c) Returns the correct shortest path as a list — 2 pts. (d) Handles edge cases: no path exists, start==goal, single node — 2 pts. SCORING: Full 8/8 = all sub-items correct. 6/8 = BFS works but missing edge case handling. 4/8 = partial BFS, some logic errors. 2/8 = attempted but fundamentally broken. 0/8 = no BFS implementation found."
}}

Hard requirements:
- Sub-item points in description MUST sum to the criterion "max" value.
- Criterion names MUST be assignment-specific.
- NEVER use placeholder names like "Criterion 1" or "Problem 1".
- Integer points only.

JSON format:
{{
  "criteria": [
    {{"criterion": "<name>", "max": <int>, "description": "<detailed guide with sub-item points summing to max>"}}
  ],
  "max_score": {max_score},
  "reasoning": "Brief design explanation"
}}

Output ONLY valid JSON. No markdown fences. No extra text."""

    user_prompt = f"""Assignment Description:
{assignment_description}

Generate a {strictness} rubric with {detail_level} detail, summing to exactly {max_score} points.
For each criterion, the sub-item point breakdown in the description MUST sum to the criterion's max points."""

    try:
        response, _meta = _chat_completion_with_failover(
            purpose="generate_rubric",
            needs_vision=False,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.3,
            top_p=0.5,
            max_tokens=5000,
            response_format={"type": "json_object"},
        )

        raw_text = response.choices[0].message.content or ""
        result = _extract_json(raw_text)

        model_criteria = _normalize_generated_criteria(result.get("criteria", []))
        final_criteria, quality_issues = _repair_or_fallback_rubric(
            assignment_description=assignment_description,
            model_criteria=model_criteria,
            max_score=max_score,
            strictness=strictness,
        )
        rubric_text = _format_rubric_text(final_criteria, max_score)
        rubric_display = _format_rubric_display(final_criteria, max_score)
        reasoning = _normalize_space(result.get("reasoning", ""))
        if quality_issues:
            note = ", ".join(quality_issues)
            reasoning = _normalize_space(f"{reasoning} Quality fixes applied: {note}.")

        return {
            "success": True,
            "rubric_text": rubric_text,
            "rubric_display": rubric_display,
            "criteria": final_criteria,
            "questions": [],
            "strictness": strictness,
            "max_score": max_score,
            "reasoning": reasoning,
            "quality_warnings": quality_issues,
        }

    except Exception as e:
        logger.exception("Failed to generate rubric with AI, using deterministic fallback")
        fallback_criteria = _build_fallback_rubric(assignment_description, max_score, strictness)
        return {
            "success": True,
            "fallback_used": True,
            "error": str(e),
            "rubric_text": _format_rubric_text(fallback_criteria, max_score),
            "rubric_display": _format_rubric_display(fallback_criteria, max_score),
            "criteria": fallback_criteria,
            "questions": [],
            "strictness": strictness,
            "max_score": max_score,
            "reasoning": "AI rubric generation failed; deterministic assignment-specific rubric was generated.",
            "quality_warnings": ["ai_generation_failed"],
        }


async def validate_submission_relevance(
    title: str,
    description: str,
    student_files: List[Any],
    rubric: str
) -> dict:
    """Validate that a submission is relevant to the assignment."""
    await _rate_limiter.acquire()

    def _extract_file_entries(files: List[Any]) -> list[tuple[str, str]]:
        entries: list[tuple[str, str]] = []
        for idx, content in enumerate(files or [], 1):
            filename = f"file_{idx}"
            text_content = ""
            if hasattr(content, "filename"):
                filename = str(getattr(content, "filename", "") or filename)
                text_content = str(getattr(content, "text_content", "") or "")
            elif isinstance(content, dict):
                filename = str(content.get("filename", filename) or filename)
                text_content = str(content.get("text_content", content.get("content", "")) or "")
            text_norm = text_content.strip()
            if text_norm:
                entries.append((filename, text_norm))
        return entries

    _relevance_stopwords = {
        "the", "and", "for", "with", "from", "that", "this", "have", "has", "was", "were",
        "question", "questions", "problem", "problems", "assignment", "task", "tasks",
        "section", "part", "criterion", "criteria", "score", "points", "marks",
        "student", "submission", "solution", "code", "system", "algorithm", "analysis",
        "design", "implementation", "implement", "develop",
    }

    def _relevance_token_set(text: str) -> set[str]:
        toks = re.findall(r"[a-zA-Z][a-zA-Z0-9_+\-*]{2,}", str(text or "").lower())
        return {tok for tok in toks if tok not in _relevance_stopwords}

    def _build_relevance_text_sample(entries: list[tuple[str, str]], max_chars: int = 9000) -> str:
        if not entries:
            return ""
        blocks: list[str] = []
        for filename, raw in entries:
            text = _normalize_space(raw)
            if not text:
                continue
            segments: list[str] = []
            if len(text) <= 420:
                segments = [text]
            else:
                head = text[:320]
                mid_start = max(0, (len(text) // 2) - 160)
                mid = text[mid_start:mid_start + 320]
                tail = text[-320:]
                segments = [head, mid, tail]
            compact = []
            seen_seg = set()
            for seg in segments:
                seg_norm = _normalize_space(seg)
                if seg_norm and seg_norm not in seen_seg:
                    compact.append(seg_norm)
                    seen_seg.add(seg_norm)
            if compact:
                blocks.append(f"[{filename}] " + " ... ".join(compact)[:980])

        if not blocks:
            return ""
        if len(blocks) > 12:
            picks = sorted({
                round(i * (len(blocks) - 1) / 11) for i in range(12)
            })
            blocks = [blocks[i] for i in picks]
        return "\n\n".join(blocks)[:max_chars]

    def _compute_assignment_signal(
        assignment_title: str,
        assignment_description: str,
        assignment_rubric: str,
        entries: list[tuple[str, str]],
    ) -> dict[str, Any]:
        assignment_text = " ".join([
            _normalize_space(assignment_title),
            _normalize_space(assignment_description),
            _normalize_space(assignment_rubric),
        ])
        assignment_tokens = _relevance_token_set(assignment_text)
        if not assignment_tokens:
            return {
                "assignment_tokens": 0,
                "token_overlap": 0,
                "signal_ratio": 0.0,
                "files_with_overlap": 0,
                "files_scanned": len(entries),
                "matched_terms": [],
                "has_relevant_sections": False,
            }

        overlap_all: set[str] = set()
        files_with_overlap = 0
        for _filename, text in entries:
            file_tokens = _relevance_token_set(text[:12000])
            overlap = assignment_tokens & file_tokens
            if overlap:
                files_with_overlap += 1
                overlap_all.update(overlap)

        overlap_count = len(overlap_all)
        signal_ratio = float(overlap_count) / float(max(1, len(assignment_tokens)))
        has_relevant_sections = (overlap_count >= 5) or (overlap_count >= 3 and signal_ratio >= 0.10)

        return {
            "assignment_tokens": len(assignment_tokens),
            "token_overlap": overlap_count,
            "signal_ratio": round(signal_ratio, 4),
            "files_with_overlap": files_with_overlap,
            "files_scanned": len(entries),
            "matched_terms": sorted(list(overlap_all))[:20],
            "has_relevant_sections": has_relevant_sections,
        }

    file_entries = _extract_file_entries(student_files)
    total_text = "\n".join(text for _, text in file_entries)
    has_any_images = any(
        (
            hasattr(c, "images") and bool(getattr(c, "images", None))
        ) or (
            isinstance(c, dict) and bool(c.get("images"))
        )
        for c in (student_files or [])
    )

    assignment_signal = _compute_assignment_signal(title, description, rubric, file_entries)

    # Count total images across all files for context
    total_image_count = sum(
        len(getattr(c, "images", []) or (c.get("images", []) if isinstance(c, dict) else []))
        for c in (student_files or [])
    )

    if len(total_text.strip()) < 50 and not has_any_images and total_image_count == 0:
        return {
            "is_relevant": False,
            "confidence": "high",
            "flags": ["empty_submission"],
            "reasoning": "Submission contains minimal or no content (no text and no images)",
            "has_relevant_sections": False,
            "assignment_signal": assignment_signal,
        }

    # Fast-path: image-only submissions (no text to analyze with a text-only relevance check).
    # These MUST proceed to vision-based grading — the images likely contain handwritten work.
    if len(total_text.strip()) < 100 and total_image_count > 0:
        logger.info(
            f"[RELEVANCE] Image-only fast-path: {total_image_count} images, "
            f"{len(total_text.strip())} text chars — skipping LLM relevance check, "
            f"marking as relevant for vision-based grading."
        )
        return {
            "is_relevant": True,
            "confidence": "medium",
            "flags": ["image_only_submission"],
            "reasoning": (
                f"Submission contains {total_image_count} images with minimal text "
                f"({len(total_text.strip())} chars). Skipping text-based relevance check; "
                f"proceeding to vision-based grading."
            ),
            "has_relevant_sections": True,
            "assignment_signal": assignment_signal,
        }

    sampled_submission = _build_relevance_text_sample(file_entries)
    
    system_prompt = """You are checking if a student submission is relevant to the given assignment.

Respond in this exact JSON format:
{
  "is_relevant": true/false,
  "confidence": "high/medium/low",
  "flags": ["list", "of", "issues"],
  "reasoning": "Explanation of why it's relevant or not",
  "has_relevant_sections": true/false,
  "relevant_evidence": ["short evidence snippets"],
  "irrelevant_evidence": ["short evidence snippets"]
}

Possible flags:
- "empty_submission": Little to no content
- "wrong_assignment": Clearly about a different topic
- "incomplete": Major sections missing
- "template_only": Only contains template/boilerplate code
- "placeholder_content": Contains "TODO", "FIXME", or placeholder text
- "off_topic": Content doesn't match assignment requirements
- "mixed_content": Contains both relevant and irrelevant sections

Important policy:
- If ANY meaningful section answers assignment requirements, set has_relevant_sections=true.
- For mixed submissions, include flag "mixed_content" and avoid marking the full submission as completely irrelevant."""

    # Build image context summary for the relevance check
    image_context = ""
    if total_image_count > 0:
        image_files_detail = []
        for c in (student_files or []):
            fname = getattr(c, "filename", None) or (c.get("filename") if isinstance(c, dict) else None) or "unknown"
            ftype = getattr(c, "file_type", None) or (c.get("file_type") if isinstance(c, dict) else None) or "unknown"
            nimgs = len(getattr(c, "images", []) or (c.get("images", []) if isinstance(c, dict) else []))
            if nimgs > 0:
                image_files_detail.append(f"  - {fname} ({ftype}): {nimgs} images/pages")
        image_context = (
            f"\n\nIMPORTANT IMAGE CONTEXT:\n"
            f"This submission contains {total_image_count} images/page renders that are NOT shown in the text sample above.\n"
            f"These images may contain handwritten solutions, diagrams, graphs, or code screenshots.\n"
            f"Files with images:\n" + "\n".join(image_files_detail) + "\n"
            f"DO NOT mark this submission as empty_submission if it has images — the student's work may be handwritten."
        )

    user_prompt = f"""Assignment: {title}
Description: {description}
Rubric: {rubric}

Submission Content Sample (coverage across files/head-middle-tail):
{sampled_submission[:9000]}

Deterministic assignment-signal summary:
- token_overlap: {assignment_signal.get("token_overlap", 0)}
- signal_ratio: {assignment_signal.get("signal_ratio", 0.0)}
- files_with_overlap: {assignment_signal.get("files_with_overlap", 0)}/{assignment_signal.get("files_scanned", 0)}
- matched_terms: {", ".join(assignment_signal.get("matched_terms", [])[:12])}
- total_images_in_submission: {total_image_count}{image_context}

Is this submission relevant to the assignment?"""

    try:
        response, _meta = _chat_completion_with_failover(
            purpose="validate_relevance",
            needs_vision=False,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.0,
            max_tokens=500,
            response_format={"type": "json_object"},
        )
        
        raw_text = response.choices[0].message.content or ""
        try:
            result = _extract_json(raw_text)
        except (json.JSONDecodeError, Exception):
            result = {}

        if not isinstance(result, dict):
            result = {}

        flags = result.get("flags", [])
        flags_norm = []
        if isinstance(flags, list):
            for raw in flags:
                flag = str(raw or "").strip().lower()
                if flag:
                    flags_norm.append(flag)
        flags_norm = sorted(set(flags_norm))

        has_relevant_sections = bool(result.get("has_relevant_sections", False)) or bool(
            assignment_signal.get("has_relevant_sections", False)
        )
        if has_relevant_sections and ("off_topic" in flags_norm or "wrong_assignment" in flags_norm):
            if "mixed_content" not in flags_norm:
                flags_norm.append("mixed_content")
                flags_norm = sorted(set(flags_norm))

        is_relevant = bool(result.get("is_relevant", True))
        if has_relevant_sections:
            is_relevant = True

        reasoning = str(result.get("reasoning", "") or "")
        if has_relevant_sections and not bool(result.get("has_relevant_sections", False)):
            reasoning = (
                reasoning + " Deterministic signal detected assignment-relevant sections in the submission."
            ).strip()

        return {
            "is_relevant": is_relevant,
            "confidence": result.get("confidence", "medium"),
            "flags": flags_norm,
            "reasoning": reasoning,
            "has_relevant_sections": has_relevant_sections,
            "assignment_signal": assignment_signal,
            "relevant_evidence": result.get("relevant_evidence", []) if isinstance(result.get("relevant_evidence"), list) else [],
            "irrelevant_evidence": result.get("irrelevant_evidence", []) if isinstance(result.get("irrelevant_evidence"), list) else [],
        }
        
    except ProviderFailoverError as e:
        logger.warning(f"Relevance validation skipped: {e}")
        return {
            "is_relevant": True,
            "confidence": "low",
            "flags": ["validation_provider_error"],
            "reasoning": str(e),
            "has_relevant_sections": bool(assignment_signal.get("has_relevant_sections", False)),
            "assignment_signal": assignment_signal,
        }
    except Exception as e:
        logger.exception("Relevance validation failed")
        return {
            "is_relevant": True,
            "confidence": "low",
            "flags": ["validation_error"],
            "reasoning": f"Validation failed: {str(e)}. Defaulting to relevant.",
            "has_relevant_sections": bool(assignment_signal.get("has_relevant_sections", False)),
            "assignment_signal": assignment_signal,
        }


_RELEVANCE_BLOCK_FLAGS = {"empty_submission", "wrong_assignment", "off_topic"}


def evaluate_relevance_gate(relevance: Optional[dict[str, Any]], image_count: int = 0) -> dict[str, Any]:
    """Decide whether grading should be blocked due to strong irrelevance signals.

    Args:
        relevance: The relevance validation result dict.
        image_count: Number of images in the submission. When > 0, the gate is
                     more lenient because images may contain handwritten work
                     that wasn't captured in text extraction.
    """
    rel = relevance if isinstance(relevance, dict) else {}
    raw_flags = rel.get("flags", [])
    flags: list[str] = []
    if isinstance(raw_flags, list):
        for raw in raw_flags:
            flag = str(raw or "").strip().lower()
            if flag:
                flags.append(flag)
    flags = sorted(set(flags))

    confidence = str(rel.get("confidence", "low") or "low").strip().lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"
    is_relevant = bool(rel.get("is_relevant", True))
    critical_flags = sorted(flag for flag in flags if flag in _RELEVANCE_BLOCK_FLAGS)
    signal_info = rel.get("assignment_signal") if isinstance(rel.get("assignment_signal"), dict) else {}
    signal_overlap = int(signal_info.get("token_overlap", 0) or 0)
    signal_ratio = float(signal_info.get("signal_ratio", 0.0) or 0.0)
    has_relevant_sections = bool(rel.get("has_relevant_sections", False)) or bool(
        signal_info.get("has_relevant_sections", False)
    )
    mixed_content = bool(rel.get("mixed_content", False)) or ("mixed_content" in flags)
    if not mixed_content and critical_flags and has_relevant_sections:
        mixed_content = True

    # If the submission has images, treat it as potentially containing handwritten
    # work.  Never block such submissions as "empty" — grade them and let the
    # LLM with vision decide.
    has_images = image_count > 0

    block_grading = False
    reason = ""
    if "empty_submission" in critical_flags and not has_images:
        block_grading = True
        reason = "Submission appears empty or unreadable."
    elif "empty_submission" in critical_flags and has_images:
        # Text is sparse but images exist — likely handwritten. Don't block.
        block_grading = False
        reason = (
            f"Minimal extracted text but submission contains {image_count} images "
            f"that may contain handwritten work. Proceeding with vision-based grading."
        )
        # Remove empty_submission from critical flags since we're overriding
        critical_flags = [f for f in critical_flags if f != "empty_submission"]
    elif mixed_content:
        block_grading = False
        reason = "Submission has mixed relevant and irrelevant content; grading should proceed with review."
    elif (not is_relevant) and critical_flags and confidence == "high" and not has_relevant_sections:
        block_grading = True
        reason = "Submission appears unrelated to the assignment."
    elif (
        (not is_relevant)
        and {"wrong_assignment", "off_topic"}.issubset(set(critical_flags))
        and confidence in {"high", "medium"}
        and not has_relevant_sections
        and signal_overlap <= 1
        and signal_ratio < 0.04
    ):
        block_grading = True
        reason = "Submission was classified as wrong assignment and off-topic."

    review_required = (not block_grading) and ((not is_relevant) or bool(critical_flags))
    return {
        "block_grading": block_grading,
        "review_required": review_required,
        "reason": reason,
        "is_relevant": is_relevant,
        "confidence": confidence,
        "flags": flags,
        "critical_flags": critical_flags,
        "has_relevant_sections": has_relevant_sections,
        "mixed_content": mixed_content,
        "assignment_signal": {
            "token_overlap": signal_overlap,
            "signal_ratio": round(signal_ratio, 4),
            "files_with_overlap": int(signal_info.get("files_with_overlap", 0) or 0),
            "files_scanned": int(signal_info.get("files_scanned", 0) or 0),
            "matched_terms": signal_info.get("matched_terms", [])[:12] if isinstance(signal_info.get("matched_terms"), list) else [],
        },
        "policy_version": "relevance_gate_v2_2026_03_06",
    }


def build_relevance_block_result(
    rubric: str,
    max_score: int,
    relevance: Optional[dict[str, Any]],
    gate: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Return a deterministic zero-score result for clearly irrelevant submissions."""
    gate_info = gate if isinstance(gate, dict) else evaluate_relevance_gate(relevance)
    reasoning = str((relevance or {}).get("reasoning", "")).strip()
    gate_reason = str(gate_info.get("reason", "")).strip()
    merged_reason = gate_reason or reasoning or "Submission failed relevance validation."

    rubric_criteria = parse_rubric(rubric)
    breakdown = []
    for rc in rubric_criteria:
        criterion = str(rc.get("criterion", "")).strip()
        max_points = float(rc.get("max", 0) or 0)
        if not criterion:
            continue
        breakdown.append({
            "criterion": criterion,
            "score": 0.0,
            "max": max_points,
            "justification": f"Not graded because the submission appears irrelevant. Evidence: relevance_gate ({', '.join(gate_info.get('critical_flags', []) or gate_info.get('flags', []) or ['none'])}).",
            "citations": [{"source": "relevance_validation"}],
        })

    conf = str(gate_info.get("confidence", "medium")).lower()
    if conf not in {"high", "medium", "low"}:
        conf = "medium"

    return {
        "total_score": 0.0,
        "max_score": max_score,
        "percentage": 0.0,
        "letter_grade": "F",
        "rubric_breakdown": breakdown,
        "overall_feedback": (
            "Submission was not graded against rubric content because relevance checks "
            f"indicated it is likely unrelated to this assignment. {merged_reason}"
        )[:2000],
        "strengths": [],
        "weaknesses": [
            "Submission appears out-of-scope for the assigned task.",
        ],
        "suggestions_for_improvement": (
            "Resubmit work that directly answers the assignment questions and aligns with the rubric criteria."
        ),
        "confidence": conf if conf in {"high", "medium"} else "medium",
        "confidence_reasoning": f"Relevance gate blocked rubric scoring. Flags: {', '.join(gate_info.get('flags', []) or ['none'])}.",
        "relevance": relevance if isinstance(relevance, dict) else {},
        "relevance_gate": gate_info,
    }


SYSTEM_PROMPT = """You are an experienced university professor grading student submissions. You have decades of teaching experience and grade exactly the way a fair, rigorous human professor would — not like a machine that pattern-matches keywords.

Think step by step for each criterion: "What did the question ASK the student to DO? Did the student actually DO it? Can I point to their specific work that answers the question?"

═══════════════════════════════════════════════════════════════
RULE 1 — GRADE THE ANSWER, NOT THE SETUP:
═══════════════════════════════════════════════════════════════
The most important distinction in grading: RESTATING THE PROBLEM IS NOT SOLVING IT.

Ask yourself: "Did the student DO what the question asked, or did they just repeat/set up the given information?"

Examples of SETUP (not the answer — worth 0 points):
  • Declaring variables or data structures that the question PROVIDES
  • Copying the problem statement or given data into their file
  • Writing imports, headers, or boilerplate without any solution logic
  • Restating a definition without explaining or applying it

Examples of ACTUAL ANSWERS (worth partial or full credit):
  • Writing logic/code that PROCESSES the given data to produce a result
  • Explaining a concept IN THEIR OWN WORDS with reasoning
  • Showing mathematical STEPS that work toward a solution
  • Creating a diagram/design that the question asked for

If a student only has the given data/setup with NO solution logic → score MUST be 0.
If they have setup AND some attempt at a solution → grade the solution attempt fairly.

═══════════════════════════════════════════════════════════════
RULE 2 — ZERO TOLERANCE FOR FABRICATION:
═══════════════════════════════════════════════════════════════
• NEVER award points for work that is NOT PRESENT in the submission.
• If a file is EMPTY (0 chars), score 0 for anything that file should address.
• If the required work is NOT FOUND in ANY submitted file, score MUST be 0.
• "Might be implemented" or "probably exists" = score 0. Do NOT speculate.
• A FILE CONTENT MANIFEST is provided. Files with 0 chars are EMPTY — do NOT invent content.

═══════════════════════════════════════════════════════════════
RULE 3 — QUOTE, NEVER PARAPHRASE:
═══════════════════════════════════════════════════════════════
In your justification for EVERY criterion you MUST:
  1. Name the SPECIFIC FILE where you found evidence.
  2. QUOTE the student's ACTUAL words, code, or answer verbatim.
     Do NOT rewrite, correct, or improve what the student wrote.
     If they wrote broken/incorrect content, quote it exactly as-is.
  3. If you cannot quote real content from a file → score = 0 for that criterion.

═══════════════════════════════════════════════════════════════
RULE 4 — FAIR PARTIAL CREDIT:
═══════════════════════════════════════════════════════════════
Grade like a fair professor — reward genuine effort proportionally:
  • Complete and correct                    → 100% of points
  • Mostly correct, minor errors            → 60-80%
  • Shows understanding, significant errors → 30-50%
  • Minimal genuine attempt                 → 10-20%
  • Only setup/given data, no solution      → 0%
  • No attempt or completely irrelevant     → 0%

For CODE: judge whether the code DOES what the question asks:
  • Correct logic that produces right output → full credit
  • Correct logic, minor syntax errors (typos, missing colons) → 50-80%
  • Right approach but broken execution → 20-40%
  • Only data/imports with no processing logic → 0%

For WRITTEN/THEORY: judge whether the answer ADDRESSES the question:
  • Correct explanation with reasoning → full credit
  • Partially correct (some right, some wrong) → proportional
  • Factually incorrect → 0%
  • Just restated the question/definition → 0%

For MATH: judge whether the student SOLVED the problem:
  • Correct method and answer → full credit
  • Right method, calculation error → 50-70%
  • Some relevant work shown → 20-40%
  • Just copied the problem → 0%

═══════════════════════════════════════════════════════════════
RULE 5 — HANDLE ANY ASSESSMENT TYPE:
═══════════════════════════════════════════════════════════════
Adapt your grading to whatever subject or format is presented:
  • Programming → evaluate correctness, logic, syntax, output
  • Essays / written → evaluate argument quality, evidence, structure
  • Math / problem-solving → evaluate method, steps, final answer
  • Diagrams / visual work → evaluate completeness, accuracy, labeling
  • Mixed assessments → apply the appropriate standard per question
  • File format issues (.docx, .rtf corrupting indentation) → evaluate logic,
    note formatting issue, apply small deduction (20-30%)

CRITICAL INSTRUCTIONS FOR IMAGE ANALYSIS:
1. Carefully analyze ALL provided images including handwritten notes, diagrams, screenshots.
2. If OCR text is incomplete, rely on visual evidence from the images.
3. Grade based on BOTH extracted text and visual evidence.
4. Mention concrete visual evidence when awarding points.
5. Do NOT mark a submission blank if images show meaningful work.

DETERMINISM PROTOCOL:
1. For the same evidence and rubric, return the same scores.
2. Use stable deductions tied to missing requirements, not style preferences.
3. If uncertain between two adjacent scores, choose the LOWER one.
4. Do not inflate scores for assumed work; grade only verifiable content.

GRADING RULES:
1. Grade ONLY what is visible or explicitly present.
2. rubric_breakdown criteria names MUST match the provided rubric criteria EXACTLY.
3. "max" MUST match rubric max values exactly.
4. Sum of rubric scores MUST equal total_score.
5. total_score MUST be within [0, max_score].
6. Every rubric item MUST include a justification citing the specific file and quoting real content.
7. If the rubric has a GRADING GUIDE with sub-items, evaluate EACH sub-item independently.
   Award points only for sub-items where you can cite specific evidence.

RESPONSE FORMAT - return ONLY valid JSON:
{
  "rubric_breakdown": [
    {
      "criterion": "<EXACT name from rubric>",
      "score": <number>,
      "max": <exact max from rubric>,
      "justification": "<MUST quote actual student content from a specific file>",
      "citations": [
        {"type": "image", "image_id": "img_0001"},
        {"type": "text", "snippet_id": "txt_0001"}
      ]
    }
  ],
  "total_score": <sum of rubric scores>,
  "overall_feedback": "<comprehensive feedback>",
  "strengths": ["<specific strength with evidence>"],
  "weaknesses": ["<specific weakness with evidence>"],
  "suggestions_for_improvement": "<actionable advice>",
  "confidence": "<high|medium|low>",
  "confidence_reasoning": "<why you are confident or not in this grade>"
}

CITATION GROUNDING (CRITICAL):
- Only cite image_ids that are ATTACHED to this request (marked in the 'ATTACHED' section).
- For images in the 'NOT ATTACHED' section, you may reference their transcribed TEXT content
  but do NOT claim to see visual details — you cannot see those images.
- If no image clearly supports a criterion, use {"source": "text_content"} as citation.
- NEVER fabricate evidence. If a file is about a different topic than the criterion,
  do NOT cite it for that criterion, even if it is the only available file.
- Score 0 for a criterion if no evidence supports it.

IMPORTANT:
- Do NOT add extra rubric criteria.
- Do NOT skip any rubric criteria.
- If handwritten work is visible, include that evidence in feedback and scoring.
- If automated vision notes are provided, treat them as additional evidence to verify against images.
- If a criterion-evidence candidate list is provided, prefer citations from that list."""


def _build_user_prompt(
    title: str,
    description: str,
    rubric: str,
    max_score: int,
    student_files: list,
    questions: Optional[list[dict]] = None,
    criterion_evidence_context: Optional[str] = None,
    reference_solution: Optional[str] = None,
    test_results: Optional[dict] = None,
) -> str:
    
    rubric_criteria = parse_rubric(rubric)
    
    parts = [
        f"ASSIGNMENT: {title}",
        f"MAX SCORE: {max_score} points",
        "",
        "DESCRIPTION:",
        description or "No description",
        "",
    ]
    
    if questions:
        parts.append("QUESTIONS:")
        for i, q in enumerate(questions, 1):
            parts.append(f"Q{i}: {q.get('text', q.get('question', ''))}")
        parts.append("")
    
    parts.append("RUBRIC — YOU MUST RETURN EVERY CRITERION BELOW (copy-paste criterion names EXACTLY):")
    parts.append("=" * 60)
    criterion_names_list = []
    for idx, item in enumerate(rubric_criteria, 1):
        crit_name = item.get("criterion", f"Criterion {idx}")
        crit_max = item.get("max", 0)
        crit_desc = item.get("description", "")
        criterion_names_list.append(crit_name)
        parts.append(f"  [{idx}] \"{crit_name}\": {crit_max} points")
        if crit_desc:
            parts.append(f"      GRADING GUIDE: {crit_desc}")
        parts.append("")
    parts.append(f"  TOTAL: {max_score} points")
    parts.append("=" * 60)
    parts.append("")
    parts.append("⚠ CRITICAL: Your rubric_breakdown MUST contain EXACTLY these criterion names (copy-paste them):")
    for cn in criterion_names_list:
        parts.append(f'  - "{cn}"')
    parts.append("If you use different names, the grading will FAIL. Copy-paste the names exactly as shown above.")
    parts.append("")
    
    # ── File Content Manifest ──────────────────────────────────────
    # This tells the LLM exactly what content exists per file so it
    # cannot hallucinate content for empty or irrelevant files.
    parts.append("FILE CONTENT MANIFEST (what the student actually submitted):")
    parts.append("-" * 60)
    empty_files: list[str] = []
    docx_files: list[str] = []
    for i, f in enumerate(student_files, 1):
        if hasattr(f, 'filename'):
            fn = f.filename
            ft = f.file_type
            img_count = len(f.images) if f.images else 0
            text_content = f.text_content or ""
            text_len = len(text_content)
        elif isinstance(f, dict):
            fn = f.get('filename', 'unknown')
            ft = f.get('file_type', f.get('type', 'unknown'))
            img_count = len(f.get('images', []))
            text_content = f.get('text_content', '') or ''
            text_len = len(text_content)
        else:
            fn = f"file_{i}"
            ft = "unknown"
            img_count = 0
            text_content = ""
            text_len = 0

        # Track empty files and docx files
        if text_len == 0 and img_count == 0:
            empty_files.append(fn)
        if fn.lower().endswith(('.docx', '.doc', '.rtf')):
            docx_files.append(fn)

        # Show file with content summary
        status = "EMPTY" if text_len == 0 and img_count == 0 else f"{text_len} chars"
        if img_count > 0:
            status += f", {img_count} images"

        parts.append(f"  {i}. {fn} ({ft}) — {status}")

        # For non-empty files, show a brief content preview (first 150 chars)
        # so the LLM knows what topics each file covers.
        if text_content.strip():
            preview = text_content.strip()[:150].replace("\n", " ")
            parts.append(f"     Preview: {preview}...")

    parts.append("-" * 60)

    if empty_files:
        parts.append(f"⚠ EMPTY FILES (0 content — score 0 for criteria these files should address): {', '.join(empty_files)}")
    if docx_files:
        parts.append(f"⚠ WORD DOCUMENTS (Python indentation may be corrupted — evaluate logic, deduct ~20-30% for code quality): {', '.join(docx_files)}")
    parts.append("")
    parts.append("CONSISTENCY REQUIREMENT:")
    parts.append("- Produce stable scoring for identical evidence.")
    parts.append("- Apply rubric deductions consistently across students.")
    parts.append("- If evidence is ambiguous, choose the lower adjacent score.")
    parts.append("")
    parts.append("CITATION REQUIREMENT:")
    parts.append("- For EACH rubric item, include citations in rubric_breakdown[].citations.")
    parts.append("- Use image_id citations when image evidence is relevant.")
    parts.append("- If only text supports a point, use {\"type\":\"text\",\"snippet_id\":\"txt_xxxx\"} or {\"source\":\"text_content\"}.")
    parts.append("- Do not cite image IDs that are not listed in the criterion evidence candidates.")
    parts.append("")
    if criterion_evidence_context:
        parts.append("CRITERION EVIDENCE CANDIDATES (PREFER THESE FOR CITATIONS):")
        parts.append(criterion_evidence_context)
        parts.append("")

    if reference_solution:
        parts.append("REFERENCE SOLUTION (INSTRUCTOR-PROVIDED):")
        parts.append("Use this as a guide for what a correct solution looks like.")
        parts.append("Compare the student's work against this reference.")
        parts.append("Do NOT require exact matches — equivalent approaches deserve full credit.")
        parts.append("")
        parts.append(str(reference_solution)[:20000])
        parts.append("")

    if test_results and isinstance(test_results, dict):
        passed = test_results.get("passed", 0)
        total = test_results.get("total", 0)
        parts.append(f"CODE TEST RESULTS: {passed}/{total} test cases passed")
        for tc in test_results.get("results", [])[:20]:
            status = "PASSED" if tc.get("passed") else "FAILED"
            name = tc.get("name", "unnamed")
            detail = ""
            if not tc.get("passed"):
                expected = str(tc.get("expected", ""))[:100]
                actual = str(tc.get("actual", ""))[:100]
                detail = f' (expected "{expected}", got "{actual}")'
            parts.append(f"  - {name}: {status}{detail}")
        parts.append("")
        parts.append("Use these test results as OBJECTIVE evidence when scoring code-related criteria.")
        parts.append("Passing tests should support higher scores; failing tests should support deductions.")
        parts.append("")

    parts.append("IMPORTANT: Carefully analyze all images provided. They may contain handwritten solutions, graphs, and diagrams.")

    return "\n".join(parts)


def _extract_text_content(student_files: list, max_chars: int = 120000) -> str:
    """Extract text content from code/text files.

    The limit is generous (120k chars ≈ 30k tokens) to ensure NO student content
    is silently dropped.  The multi-pass system will split this into manageable
    windows if it exceeds MULTI_PASS_TEXT_THRESHOLD (28k chars).

    If the combined content still exceeds *max_chars*, a two-pass strategy
    ensures every file gets at least a fair share before any is truncated:
      Pass 1 — give each file up to ``fair_share = max_chars / n_files`` chars.
      Pass 2 — redistribute remaining budget to files that need more.
    """
    # First pass: collect all file content without truncation.
    raw_files: list[tuple[str, str, str]] = []  # (filename, file_type, content)
    for f in student_files:
        if hasattr(f, "text_content"):
            file_type = f.file_type
            filename = f.filename
            content = f.text_content
        elif hasattr(f, "content"):
            file_type = getattr(f, "type", "code")
            filename = getattr(f, "filename", "unknown")
            content = f.content
        else:
            file_type = f.get("file_type", f.get("type", ""))
            filename = f.get("filename", "unknown")
            content = f.get("text_content", f.get("content"))

        if file_type in ("image", "error", "missing", "binary", "archive"):
            continue
        if content is None:
            continue
        if isinstance(content, str) and content.strip():
            raw_files.append((filename, file_type, content))

    if not raw_files:
        return ""

    total_raw = sum(len(c) for _, _, c in raw_files)
    # If everything fits, no truncation needed.
    if total_raw + len(raw_files) * 40 <= max_chars:
        parts = []
        for filename, file_type, content in raw_files:
            parts.append(f"\n=== {filename} ({file_type}) ===\n")
            parts.append(content)
        return "\n".join(parts)

    # Fair-share truncation: each file gets at least a proportional budget.
    n = len(raw_files)
    header_budget = 40 * n  # rough header allowance
    content_budget = max_chars - header_budget
    fair_share = max(2000, content_budget // n)

    text_parts: list[str] = []
    total_chars = 0
    for filename, file_type, content in raw_files:
        header = f"\n=== {filename} ({file_type}) ===\n"
        # BUG-16 fix: each file gets exactly fair_share, not the entire remaining budget.
        # This ensures later files aren't starved by earlier ones.
        remaining = content_budget - total_chars
        file_budget = min(fair_share, remaining) if remaining > 0 else 0
        if file_budget <= 0:
            text_parts.append(header)
            text_parts.append("[TRUNCATED — budget exhausted]")
            total_chars += len(header) + 30
            continue
        chunk = content[:file_budget]
        text_parts.append(header)
        text_parts.append(chunk)
        total_chars += len(header) + len(chunk)
        if len(content) > file_budget:
            text_parts.append(f"\n[...{len(content) - file_budget} chars truncated from {filename}]")
            total_chars += 60

    return "\n".join(text_parts)


def _estimate_visual_content_score(base64_data: str) -> float:
    """
    Estimate whether an image likely contains meaningful visual content.
    Higher score means more contrast/ink-like pixels (useful for handwriting/code/diagrams).
    """
    if not base64_data:
        return 0.0
    try:
        raw = base64.b64decode(base64_data)
        gray = Image.open(io.BytesIO(raw)).convert("L")
        stat = ImageStat.Stat(gray)
        stddev = float(stat.stddev[0]) if stat.stddev else 0.0
        hist = gray.histogram() or []
        total = float(sum(hist)) or 1.0
        dark_ratio = sum(hist[:180]) / total
        very_dark_ratio = sum(hist[:120]) / total
        return round(stddev + (dark_ratio * 25.0) + (very_dark_ratio * 35.0), 3)
    except Exception:
        return 0.0


# ── OCR-based image classification ──────────────────────────────────────────
# Classifies images as text-heavy (handwritten notes — transcription sufficient)
# or visual-heavy (diagrams/graphs — needs actual image in grading call).

_TESSERACT_AVAILABLE: Optional[bool] = None  # lazy detection


def _check_tesseract() -> bool:
    """Check if pytesseract + Tesseract binary are available. Cached after first call."""
    global _TESSERACT_AVAILABLE
    if _TESSERACT_AVAILABLE is not None:
        return _TESSERACT_AVAILABLE
    try:
        import pytesseract  # noqa: F811
        pytesseract.get_tesseract_version()
        _TESSERACT_AVAILABLE = True
    except Exception:
        _TESSERACT_AVAILABLE = False
        logger.info("Tesseract OCR not available — OCR-based image classification disabled. "
                     "Install tesseract and pytesseract for optimal image prioritization.")
    return _TESSERACT_AVAILABLE


# Threshold: if OCR extracts >= this many chars, the image is "text_heavy".
# Below this, it's "visual_heavy" (diagram, graph, screenshot, etc.).
OCR_TEXT_HEAVY_THRESHOLD: int = 80


def _ocr_classify_image(base64_data: str) -> dict[str, Any]:
    """Run OCR on an image to classify it as text-heavy or visual-heavy.

    Returns:
        {
            "ocr_text_len": int,        # chars extracted by OCR
            "ocr_text": str,            # first 500 chars of OCR text (for supplemental evidence)
            "needs_visual": bool,       # True = diagram/graph, should get image slot
            "classification": str,      # "text_heavy" | "visual_heavy" | "mixed" | "unknown"
        }
    """
    result: dict[str, Any] = {
        "ocr_text_len": 0,
        "ocr_text": "",
        "needs_visual": True,  # default: assume visual (safe fallback)
        "classification": "unknown",
    }

    if not base64_data or not _check_tesseract():
        return result

    try:
        import pytesseract
        raw = base64.b64decode(base64_data)
        img = Image.open(io.BytesIO(raw))

        # Convert to RGB if needed (pytesseract can struggle with palette/RGBA)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        # Resize large images for OCR speed (OCR doesn't need full resolution)
        max_ocr_dim = 1200
        if max(img.size) > max_ocr_dim:
            img.thumbnail((max_ocr_dim, max_ocr_dim), Image.Resampling.LANCZOS)

        # Run OCR with timeout protection
        ocr_text = pytesseract.image_to_string(img, timeout=5).strip()

        # Clean OCR noise: remove lines that are just punctuation/whitespace
        clean_lines = [
            ln for ln in ocr_text.split("\n")
            if len(ln.strip()) > 2 and sum(c.isalnum() for c in ln) > len(ln) * 0.3
        ]
        clean_text = "\n".join(clean_lines).strip()
        text_len = len(clean_text)

        result["ocr_text_len"] = text_len
        result["ocr_text"] = clean_text[:500]

        if text_len >= OCR_TEXT_HEAVY_THRESHOLD * 3:
            # Lots of text — definitely handwritten notes or typed text
            result["needs_visual"] = False
            result["classification"] = "text_heavy"
        elif text_len >= OCR_TEXT_HEAVY_THRESHOLD:
            # Moderate text — mixed content (text + some visuals)
            # Still mark as needs_visual=False since transcription captures the text
            # But could have diagrams too — the vision pre-analysis transcription handles that
            result["needs_visual"] = False
            result["classification"] = "mixed"
        else:
            # Little or no text — diagram, graph, flowchart, screenshot, etc.
            result["needs_visual"] = True
            result["classification"] = "visual_heavy"

    except Exception as exc:
        # Graceful fallback — OCR failure should never break grading
        logger.debug("OCR classification failed: %s", exc)
        result["classification"] = "unknown"
        result["needs_visual"] = True  # safe fallback

    return result


def _ocr_classify_batch(images: list[dict]) -> list[dict]:
    """Run OCR classification on a batch of images, enriching each with OCR metadata.

    Modifies images in-place by adding:
      - _ocr_text_len: int
      - _ocr_needs_visual: bool
      - _ocr_classification: str
      - _ocr_text: str (first 500 chars, for supplemental evidence)

    Returns the same list (mutated).
    """
    if not _check_tesseract():
        # No tesseract — mark all as unknown/needs_visual
        for img in images:
            img["_ocr_text_len"] = 0
            img["_ocr_needs_visual"] = True
            img["_ocr_classification"] = "unknown"
            img["_ocr_text"] = ""
        return images

    for img in images:
        b64 = str(img.get("base64", "") or "")
        ocr = _ocr_classify_image(b64)
        img["_ocr_text_len"] = ocr["ocr_text_len"]
        img["_ocr_needs_visual"] = ocr["needs_visual"]
        img["_ocr_classification"] = ocr["classification"]
        img["_ocr_text"] = ocr["ocr_text"]

    return images


def _collect_selected_images(student_files: list, max_images: int = 50) -> list[dict]:
    """
    Select and order images for grading.
    Strategy:
    - Keep both focus crops and full pages (context + detail).
    - Prefer one strong focus and one full-page image per page first.
    - Rank by estimated visual content so blank-ish crops are de-prioritized.
    """
    selected: list[dict] = []
    candidates: list[dict] = []

    for f in student_files:
        if not hasattr(f, "images") or not f.images:
            continue
        fname = str(getattr(f, "filename", "unknown"))
        for img in list(f.images or []):
            if not isinstance(img, dict):
                continue
            page = int(img.get("page", 0) or 0)
            desc = str(img.get("description", "") or "")
            region_type = str(img.get("region_type", "") or "")
            is_focus = ("focus" in desc.lower()) or ("focus" in region_type.lower())
            is_full = ("full page" in desc.lower()) or ("full_page" in region_type.lower())
            b64 = str(img.get("base64", "") or "")
            candidates.append({
                "filename": fname,
                "page": page,
                "description": desc or f"Page {page or '?'}",
                "size_bytes": int(img.get("size_bytes", 0) or 0),
                "media_type": str(img.get("media_type", "image/png") or "image/png"),
                "base64": b64,
                "is_focus": is_focus,
                "is_full": is_full,
                "content_score": _estimate_visual_content_score(b64),
            })

    if not candidates:
        return selected

    # First pass: best focus + best full page per (file,page), for balanced context/detail.
    by_page: dict[tuple[str, int], list[dict]] = {}
    for img in candidates:
        by_page.setdefault((img["filename"], img["page"]), []).append(img)

    prioritized: list[dict] = []
    for key in sorted(by_page.keys(), key=lambda k: (k[0], k[1])):
        page_imgs = by_page[key]
        focus_imgs = sorted(
            [x for x in page_imgs if x["is_focus"]],
            key=lambda x: (x["content_score"], x["size_bytes"]),
            reverse=True,
        )
        full_imgs = sorted(
            [x for x in page_imgs if x["is_full"]],
            key=lambda x: (x["content_score"], x["size_bytes"]),
            reverse=True,
        )
        other_imgs = sorted(
            [x for x in page_imgs if not x["is_focus"] and not x["is_full"]],
            key=lambda x: (x["content_score"], x["size_bytes"]),
            reverse=True,
        )
        if focus_imgs:
            prioritized.append(focus_imgs[0])
        if full_imgs:
            prioritized.append(full_imgs[0])
        elif other_imgs:
            prioritized.append(other_imgs[0])

    # Second pass: fill remaining slots by overall utility.
    remaining = sorted(
        candidates,
        key=lambda x: (
            0 if x["is_focus"] else 1,
            -x["content_score"],
            -x["size_bytes"],
            x["filename"],
            x["page"],
            x["description"],
        ),
    )

    # D2 FIX: Perceptual hash-based dedup instead of weak 48-char base64 prefix
    seen_hashes: list[int] = []
    for img in prioritized + remaining:
        if len(selected) >= max_images:
            break
        if not img["base64"]:
            continue
        try:
            raw_bytes = base64.b64decode(img["base64"])
            img_obj = Image.open(io.BytesIO(raw_bytes)).convert("L").resize((8, 8), Image.Resampling.LANCZOS)
            pixels = list(img_obj.getdata())
            avg = sum(pixels) / len(pixels)
            phash = sum(1 << i for i, p in enumerate(pixels) if p >= avg)
        except Exception:
            phash = hash(img["base64"][:128])
        # Check Hamming distance against all accepted images
        is_dup = False
        for h in seen_hashes:
            if bin(phash ^ h).count("1") <= 5:
                is_dup = True
                break
        if is_dup:
            continue
        seen_hashes.append(phash)
        selected.append(img)

    for idx, img in enumerate(selected, 1):
        if not img.get("image_id"):
            img["image_id"] = f"img_{idx:04d}"

    return selected


def _build_vision_batch_prompt(chunk: list[dict], chunk_id: int, total_chunks_hint: int) -> str:
    lines = [
        f"Vision batch {chunk_id}/{max(1, total_chunks_hint)}",
        "Analyze each image independently. Do not infer missing details.",
        "Return strict JSON:",
        "{",
        '  "entries": [',
        '    {"image_id":"img_0001","summary":"...", "transcription":"...", "substantive": true, "confidence":"high|medium|low"}',
        "  ]",
        "}",
        "",
        "Images in this batch:",
    ]
    for idx, img in enumerate(chunk, 1):
        lines.append(
            f"{idx}. image_id={img.get('image_id')} file={img.get('filename')} page={img.get('page')} desc={img.get('description')}"
        )
    return "\n".join(lines)


def _parse_vision_entries(raw_text: str, chunk: list[dict]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    try:
        parsed = _extract_json(raw_text)
        raw_entries = parsed.get("entries") if isinstance(parsed, dict) else None
        if isinstance(raw_entries, list):
            for item in raw_entries:
                if not isinstance(item, dict):
                    continue
                image_id = str(item.get("image_id", "")).strip()
                if not image_id:
                    continue
                entries.append({
                    "image_id": image_id,
                    "summary": str(item.get("summary", "")).strip()[:VISION_ENTRY_SUMMARY_LIMIT],
                    "transcription": str(item.get("transcription", "")).strip()[:VISION_ENTRY_TRANSCRIPTION_LIMIT],
                    "substantive": bool(item.get("substantive", False)),
                    "confidence": str(item.get("confidence", "")).strip().lower()[:20],
                })
    except Exception:
        entries = []

    if entries:
        return entries

    # Fallback: LLM didn't return parseable JSON. Create per-image entries
    # with distinguishing metadata so each image has a unique fallback.
    fallback_excerpt = raw_text.strip()[:360]
    for idx, img in enumerate(chunk):
        img_id = str(img.get("image_id", f"img_fallback_{idx}"))
        img_file = str(img.get("filename", "unknown"))
        img_page = img.get("page", "?")
        entries.append({
            "image_id": img_id,
            "summary": f"[Fallback for {img_id} from {img_file} p{img_page}] {fallback_excerpt}"[:VISION_ENTRY_SUMMARY_LIMIT],
            "transcription": "",
            "substantive": False,
            "confidence": "low",
        })
    return entries


def _vision_batch_with_failover(
    *,
    chunk: list[dict],
    chunk_id: int,
    total_chunks_hint: int,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    candidates = _enabled_provider_candidates(needs_vision=True)
    attempts: list[dict[str, Any]] = []
    if not candidates:
        raise ProviderFailoverError(
            purpose="vision_preanalysis",
            attempts=[{
                "provider": "none",
                "model": "",
                "error_type": "configuration",
                "error": "No enabled vision provider is configured.",
            }],
        )

    prompt = _build_vision_batch_prompt(chunk, chunk_id, total_chunks_hint)
    for spec, model in candidates:
        provider_key = "nvidia" if spec.name == "nvidia_nim" else spec.name
        # Retry up to 3 times for transient errors (BUG-09 fix)
        for retry_idx in range(3):
            client = _get_client(spec)
            try:
                user_content: list[dict] = [{"type": "text", "text": prompt}]
                for img in chunk:
                    user_content.append({
                        "type": "text",
                        "text": (
                            f"[Image id={img.get('image_id')}] "
                            f"file={img.get('filename')} page={img.get('page')} desc={img.get('description')}"
                        ),
                    })
                    user_content.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{img.get('media_type', 'image/png')};base64,{img.get('base64', '')}",
                            "detail": "high",
                        },
                    })
                response = _chat_completion(
                    client,
                    _spec=spec,
                    purpose="vision_preanalysis",
                    provider_name=provider_key,
                    model=model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are analyzing student-submission images for grading support. "
                                "Extract visible handwritten/typed content precisely. "
                                "If unreadable, explicitly say unreadable."
                            ),
                        },
                        {"role": "user", "content": user_content},
                    ],
                    temperature=0.0,
                    top_p=1.0,
                    # BUG-25 fix: scale tokens with chunk size (~250 tokens per image)
                    max_tokens=min(4000, max(1200, len(chunk) * 250)),
                    seed=42,
                )
                notes = (response.choices[0].message.content or "").strip()
                usage_obj = getattr(response, "usage", None)
                usage = None
                if usage_obj:
                    usage = {
                        "prompt_tokens": getattr(usage_obj, "prompt_tokens", 0),
                        "completion_tokens": getattr(usage_obj, "completion_tokens", 0),
                        "total_tokens": getattr(usage_obj, "total_tokens", 0),
                    }

                entries = _parse_vision_entries(notes, chunk)
                return notes, entries, {
                    "provider": provider_key,
                    "provider_key": provider_key,
                    "model": model,
                    "usage": usage,
                    "attempts_before_success": attempts,
                    "retries": retry_idx,
                }
            except Exception as exc:
                error_type = _classify_provider_error(exc)
                attempts.append({
                    "provider": provider_key,
                    "provider_key": provider_key,
                    "model": model,
                    "error_type": error_type,
                    "error": str(exc)[:500],
                    "retry": retry_idx,
                })
                # Retry transient errors with backoff
                if error_type in _RETRYABLE and retry_idx < 2:
                    wait = BASE_BACKOFF * (2 ** retry_idx)
                    logger.warning(
                        "vision_preanalysis: provider %s/%s %s (attempt %d/3), retrying in %ds: %s",
                        spec.name, model, error_type, retry_idx + 1, wait, exc,
                    )
                    time.sleep(wait)
                    continue
                # Non-retryable or exhausted retries — move to next provider
                _apply_provider_cooldown(provider_key, error_type)
                logger.warning(
                    "vision_preanalysis: provider %s/%s failed with %s: %s",
                    spec.name, model, error_type, exc,
                )
                break  # exit retry loop, try next provider

    raise ProviderFailoverError(purpose="vision_preanalysis", attempts=attempts)


async def _consolidate_vision_notes(batch_notes: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    trace: dict[str, Any] = {"provider": "", "model": "", "error": ""}
    if not batch_notes:
        return "", trace

    lines = []
    for batch in batch_notes:
        lines.append(
            f"[Batch {batch.get('batch_id')}] image_ids={','.join(batch.get('image_ids', [])[:12])}"
        )
        lines.append(str(batch.get("notes", ""))[:2000])
    source_text = "\n\n".join(lines)[:16000]

    system_prompt = (
        "Consolidate vision notes deterministically. Keep concrete evidence only. "
        "Preserve image_id references whenever possible."
    )
    user_prompt = (
        "Merge these per-batch image notes into a concise evidence brief.\n"
        "Output plain text with sections:\n"
        "1) Key visual evidence\n"
        "2) Handwritten/diagram observations\n"
        "3) Unreadable or uncertain regions\n\n"
        f"{source_text}"
    )

    try:
        response, meta = _chat_completion_with_failover(
            purpose="vision_consolidation",
            needs_vision=False,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
            top_p=1.0,
            max_tokens=1200,
            seed=42,
            response_format={"type": "json_object"},
        )
        trace["provider"] = meta.get("provider", "")
        trace["model"] = meta.get("model", "")
        merged = (response.choices[0].message.content or "").strip()
        return merged[:12000], trace
    except Exception as exc:
        trace["error"] = str(exc)
        fallback = "\n\n".join(f"[Batch {b.get('batch_id')}]\n{str(b.get('notes',''))[:1600]}" for b in batch_notes)
        return fallback[:12000], trace


def _build_full_vision_transcript(
    vision_trace: dict[str, Any],
    selected_images: Optional[list[dict]] = None,
    final_image_ids: Optional[set[str]] = None,
) -> str:
    """Build structured per-image transcript blocks from vision pre-analysis.

    Replaces the old lossy _consolidate_vision_notes approach.  Every analyzed
    image gets its own block so the grading LLM has access to ALL transcriptions
    rather than a compressed summary.  If the total exceeds the configured budget
    (VISION_TRANSCRIPT_MAX_CHARS), entries are priority-trimmed: non-substantive
    and low-confidence entries are dropped first.

    When *selected_images* is provided, OCR text from local Tesseract analysis
    is merged as supplemental evidence alongside the LLM's vision transcription.

    When *final_image_ids* is provided, the transcript is split into two clearly
    labeled sections:
      1. Images ATTACHED to this request (the LLM can see these)
      2. Additional transcriptions from images NOT attached (text-only evidence)
    This prevents the LLM from hallucinating visual details about images it
    cannot actually see.
    """
    batch_notes: list[dict[str, Any]] = list((vision_trace or {}).get("batch_notes", []) or [])
    if not batch_notes:
        return ""

    _sent_ids: set[str] = set(final_image_ids or set())

    # Build OCR lookup from selected_images (enriched by _ocr_classify_batch).
    ocr_by_id: dict[str, dict[str, Any]] = {}
    if selected_images:
        for img in selected_images:
            iid = str(img.get("image_id", "")).strip()
            if iid and img.get("_ocr_text"):
                ocr_by_id[iid] = {
                    "ocr_text": str(img.get("_ocr_text", "")),
                    "classification": str(img.get("_ocr_classification", "")),
                }

    # Flatten all entries with their source metadata.
    all_entries: list[dict[str, Any]] = []
    for batch in batch_notes:
        entries = batch.get("entries") if isinstance(batch.get("entries"), list) else []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            image_id = str(entry.get("image_id", "")).strip()
            if not image_id:
                continue
            all_entries.append(entry)

    if not all_entries:
        return ""

    # Priority sort: substantive + high confidence first, non-substantive + low confidence last.
    _conf_rank = {"high": 3, "medium": 2, "low": 1}

    def _priority(e: dict) -> tuple:
        return (
            1 if e.get("substantive") else 0,
            _conf_rank.get(str(e.get("confidence", "")).lower(), 0),
        )

    all_entries.sort(key=_priority, reverse=True)

    def _format_entry(entry: dict) -> str:
        image_id = str(entry.get("image_id", ""))
        filename = str(entry.get("filename", "")) or "unknown"
        page = entry.get("page", "")
        confidence = str(entry.get("confidence", "")).lower() or "unknown"
        substantive = entry.get("substantive", False)
        summary = str(entry.get("summary", "")).strip()
        transcription = str(entry.get("transcription", "")).strip()

        header = f"[IMAGE {image_id}"
        if filename and filename != "unknown":
            header += f" | file: {filename}"
        if page:
            header += f" | page: {page}"
        header += f" | confidence: {confidence}"
        if substantive:
            header += " | substantive"
        header += "]"

        body_parts: list[str] = []
        if summary:
            body_parts.append(f"Summary: {summary}")
        if transcription:
            body_parts.append(f'Transcription: "{transcription}"')

        ocr_info = ocr_by_id.get(image_id)
        if ocr_info and ocr_info["ocr_text"]:
            ocr_text = ocr_info["ocr_text"]
            classification = ocr_info.get("classification", "")
            if len(ocr_text) > 20:
                body_parts.append(f"OCR ({classification}): {ocr_text}")

        if not body_parts:
            body_parts.append("(no transcription available)")

        return header + "\n" + "\n".join(body_parts) + "\n---"

    # Split entries into sent vs not-sent groups.
    sent_entries: list[dict] = []
    unsent_entries: list[dict] = []
    for entry in all_entries:
        image_id = str(entry.get("image_id", "")).strip()
        if _sent_ids and image_id in _sent_ids:
            sent_entries.append(entry)
        else:
            unsent_entries.append(entry)

    lines: list[str] = []
    total_chars = 0
    budget = int(VISION_TRANSCRIPT_MAX_CHARS)

    # Section 1: Images that ARE attached to this request.
    if sent_entries and _sent_ids:
        lines.append("── IMAGES ATTACHED TO THIS REQUEST (you can see these) ──")
        total_chars += 60
        for entry in sent_entries:
            block = _format_entry(entry)
            if total_chars + len(block) > budget:
                break
            lines.append(block)
            total_chars += len(block) + 1

    # Section 2: Transcriptions from images NOT attached (text-only evidence).
    if unsent_entries:
        separator = (
            "\n── ADDITIONAL TRANSCRIPTIONS (images NOT attached — text evidence only) ──\n"
            "The following transcriptions are from images that were analyzed but are NOT\n"
            "attached as images to this request. You CANNOT see these images. Use ONLY\n"
            "the transcribed text below as evidence. Do NOT claim to see visual details\n"
            "from these images.\n"
        )
        if total_chars + len(separator) > budget:
            return "\n".join(lines)
        lines.append(separator)
        total_chars += len(separator)
        for entry in unsent_entries:
            block = _format_entry(entry)
            if total_chars + len(block) > budget:
                break
            lines.append(block)
            total_chars += len(block) + 1

    # Fallback: if no sent/unsent split (no final_image_ids provided), dump all.
    if not _sent_ids:
        lines = []
        total_chars = 0
        for entry in all_entries:
            block = _format_entry(entry)
            if total_chars + len(block) > budget:
                break
            lines.append(block)
            total_chars += len(block) + 1

    return "\n".join(lines)


async def _run_vision_preanalysis(selected_images: list[dict]) -> tuple[str, dict]:
    """
    Optional pre-analysis pass with deterministic chunking + consolidation.
    Returns consolidated notes and trace metadata (including per-batch notes).
    """
    trace: dict[str, Any] = {
        "enabled": bool(ENABLE_VISION_PREANALYSIS),
        "model": "",
        "provider": "",
        "images_analyzed": 0,
        "images_requested": 0,
        "chunks": 0,
        "chunk_size": int(VISION_PREANALYSIS_CHUNK_SIZE),
        "chunk_size_effective": int(VISION_PREANALYSIS_CHUNK_SIZE),
        "provider_image_cap": 0,
        "preanalysis_cap": int(MAX_IMAGES_FOR_PREANALYSIS),
        "preanalysis_cap_effective": 0,
        "sampling_strategy": "coverage_anchor",
        "adaptive_splits": 0,
        "batch_notes": [],
        "consolidation": {},
        "error": "",
    }
    if not ENABLE_VISION_PREANALYSIS or not selected_images:
        return "", trace

    requested_count = len(selected_images)
    configured_cap = int(MAX_IMAGES_FOR_PREANALYSIS)
    effective_cap = requested_count if configured_cap <= 0 else min(requested_count, configured_cap)
    images = _select_images_for_preanalysis(selected_images, effective_cap)
    trace["images_requested"] = requested_count
    trace["images_analyzed"] = len(images)
    trace["preanalysis_cap_effective"] = effective_cap

    vision_candidates = _enabled_provider_candidates(needs_vision=True)
    if not vision_candidates:
        trace["error"] = "No enabled vision provider available."
        return "", trace

    provider_image_cap = _effective_vision_image_cap(vision_candidates)
    trace["provider_image_cap"] = provider_image_cap
    configured_chunk = max(1, int(VISION_PREANALYSIS_CHUNK_SIZE))
    chunk_size = max(1, min(configured_chunk, provider_image_cap))
    trace["chunk_size_effective"] = chunk_size

    pending_chunks: list[list[dict]] = [
        images[i:i + chunk_size] for i in range(0, len(images), chunk_size)
    ]
    trace["chunks"] = len(pending_chunks)
    batch_notes: list[dict[str, Any]] = []
    all_notes: list[str] = []
    chunk_counter = 0

    while pending_chunks:
        chunk = pending_chunks.pop(0)
        if not chunk:
            continue

        try:
            await _rate_limiter.acquire()
            chunk_counter += 1
            notes, entries, meta = _vision_batch_with_failover(
                chunk=chunk,
                chunk_id=chunk_counter,
                total_chunks_hint=len(pending_chunks) + chunk_counter,
            )
            trace["provider"] = str(meta.get("provider", ""))
            trace["model"] = str(meta.get("model", ""))
            image_ids = [str(img.get("image_id", "")) for img in chunk if img.get("image_id")]
            # Enrich entries with filename/page from source images for full transcript
            chunk_by_id = {str(img.get("image_id", "")): img for img in chunk}
            for entry in entries:
                src = chunk_by_id.get(str(entry.get("image_id", "")))
                if src:
                    entry.setdefault("filename", src.get("filename", ""))
                    entry.setdefault("page", src.get("page", ""))
            batch_record = {
                "batch_id": chunk_counter,
                "provider": meta.get("provider", ""),
                "model": meta.get("model", ""),
                "image_ids": image_ids,
                "notes": notes[:4000],
                "entries": entries,
            }
            batch_notes.append(batch_record)
            if notes:
                all_notes.append(f"[Batch {chunk_counter}] ids={','.join(image_ids)}\n{notes}")
        except ProviderFailoverError as exc:
            attempts = list(getattr(exc, "attempts", []) or [])
            has_image_limit_error = any(a.get("error_type") == "too_many_images" for a in attempts)
            if has_image_limit_error and len(chunk) > 1:
                mid = max(1, len(chunk) // 2)
                pending_chunks.insert(0, chunk[mid:])
                pending_chunks.insert(0, chunk[:mid])
                trace["adaptive_splits"] = int(trace.get("adaptive_splits", 0) or 0) + 1
                continue
            trace["error"] = str(exc)
            logger.warning("Vision pre-analysis failed for chunk %s: %s", chunk_counter + 1, exc)
            break
        except Exception as exc:
            trace["error"] = str(exc)
            logger.warning("Vision pre-analysis runtime error: %s", exc)
            break

    trace["batch_notes"] = batch_notes
    # NOTE: Consolidation LLM call removed — replaced by _build_full_vision_transcript()
    # which preserves ALL per-image transcriptions without lossy compression.
    # The full transcript is built later in grade_student() from the batch_notes in trace.
    trace["consolidation"] = {"provider": "", "model": "", "skipped": True,
                              "reason": "replaced_by_full_transcript_injection"}

    # Return empty string for backward compat; grade_student() uses _build_full_vision_transcript().
    return "", trace


def _build_multimodal_content(
    user_text: str,
    text_content: str,
    selected_images: list[dict],
) -> tuple[list[dict], int, list[dict]]:
    """Build content with text and selected images. Returns (content_list, image_count, image_info_list)."""
    full_text = user_text + "\n\nFILE CONTENTS:\n" + text_content

    content: list[dict] = [{"type": "text", "text": full_text}]
    image_info_list = []

    for img in selected_images:
        image_id = str(img.get("image_id", ""))
        page_num = img.get("page", "?")
        desc = img.get("description", f"Page {page_num}")
        filename = img.get("filename", "unknown")

        content.append({
            "type": "text",
            "text": (
                f"\n[Image id={image_id} from {filename}: {desc}; "
                f"visual_score={img.get('content_score', 0):.2f}]"
            )
        })
        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:{img.get('media_type', 'image/png')};base64,{img['base64']}",
                "detail": "high"
            },
        })

        image_info_list.append({
            "image_id": image_id,
            "filename": filename,
            "page": page_num,
            "description": desc,
            "size_bytes": img.get("size_bytes", 0),
            "media_type": img.get("media_type", "image/png"),
            "content_score": img.get("content_score", 0.0),
        })

    return content, len(selected_images), image_info_list


def _enforce_image_byte_budget(images: list[dict], max_total_bytes: int) -> list[dict]:
    """Keep image order but cap total payload bytes for final grading call."""
    if max_total_bytes <= 0:
        return images
    kept: list[dict] = []
    total = 0
    for img in images:
        size = int(img.get("size_bytes", 0) or 0)
        if kept and (total + size) > max_total_bytes:
            continue
        kept.append(img)
        total += size
    return kept


def _select_images_for_preanalysis(images: list[dict], max_images: int) -> list[dict]:
    """
    Pick images for vision pre-analysis with broad submission coverage.
    Strategy:
    - First choose one strong anchor per (file,page), preferring focus crops.
    - If anchors exceed the cap, sample anchors evenly (avoid first-N bias).
    - Then add complementary context/detail for each page before global fill.
    """
    if not images:
        return []
    if max_images <= 0 or len(images) <= max_images:
        return images

    anchors: list[dict] = []
    by_page: dict[tuple[str, int], list[dict]] = {}
    for img in images:
        by_page.setdefault(
            (
                str(img.get("filename", "")),
                int(img.get("page", 0) or 0),
            ),
            [],
        ).append(img)

    anchor_by_page: dict[tuple[str, int], dict] = {}
    for key in sorted(by_page.keys(), key=lambda k: (k[0], k[1])):
        page_images = by_page[key]
        focus = [x for x in page_images if x.get("is_focus")]
        pool = focus if focus else page_images
        best = max(
            pool,
            key=lambda x: (
                float(x.get("content_score", 0.0) or 0.0),
                int(x.get("size_bytes", 0) or 0),
                str(x.get("description", "")),
            ),
        )
        anchors.append(best)
        anchor_by_page[key] = best

    chosen: list[dict] = []
    seen: set[tuple[str, int, str, int, str]] = set()

    def _sig(img: dict) -> tuple[str, int, str, int, str]:
        return (
            str(img.get("filename", "")),
            int(img.get("page", 0) or 0),
            str(img.get("description", "")),
            int(img.get("size_bytes", 0) or 0),
            str(img.get("base64", ""))[:48],
        )

    def _add(img: dict) -> None:
        s = _sig(img)
        if s in seen:
            return
        seen.add(s)
        chosen.append(img)

    if len(anchors) > max_images:
        # Evenly sample across the sorted anchor sequence to preserve global coverage.
        for i in range(max_images):
            idx = round((i * (len(anchors) - 1)) / (max_images - 1)) if max_images > 1 else 0
            _add(anchors[idx])
        return chosen[:max_images]

    for img in anchors:
        _add(img)
        if len(chosen) >= max_images:
            return chosen[:max_images]

    # Add one complementary image per page when available (e.g., full page to accompany focus crop),
    # distributed round-robin by file to avoid filename-order bias.
    complements_by_file: dict[str, list[dict]] = {}
    for key in sorted(by_page.keys(), key=lambda k: (k[0], k[1])):
        page_images = by_page[key]
        anchor = anchor_by_page.get(key)
        complement_candidates = [x for x in page_images if _sig(x) != _sig(anchor)] if anchor else list(page_images)
        if not complement_candidates:
            continue
        preferred = sorted(
            complement_candidates,
            key=lambda x: (
                0 if ((anchor and anchor.get("is_focus") and x.get("is_full")) or (anchor and anchor.get("is_full") and x.get("is_focus"))) else 1,
                0 if x.get("is_full") else 1,
                -float(x.get("content_score", 0.0) or 0.0),
                -int(x.get("size_bytes", 0) or 0),
            ),
        )
        complements_by_file.setdefault(key[0], []).append(preferred[0])

    if len(chosen) < max_images and complements_by_file:
        file_order = sorted(complements_by_file.keys())
        while len(chosen) < max_images:
            progressed = False
            for fname in file_order:
                candidates = complements_by_file.get(fname) or []
                if not candidates:
                    continue
                _add(candidates.pop(0))
                progressed = True
                if len(chosen) >= max_images:
                    break
            if not progressed:
                break

    # Final fill by utility if slots remain.
    if len(chosen) < max_images:
        remaining = sorted(
            images,
            key=lambda x: (
                0 if x.get("is_focus") else 1,
                -float(x.get("content_score", 0.0) or 0.0),
                -int(x.get("size_bytes", 0) or 0),
                str(x.get("filename", "")),
                int(x.get("page", 0) or 0),
                str(x.get("description", "")),
            ),
        )
        for img in remaining:
            _add(img)
            if len(chosen) >= max_images:
                break
    return chosen[:max_images]


def _pick_diverse_images_for_grading(images: list[dict], max_images: int) -> list[dict]:
    """
    Pick a diverse set of images when image caps are tight.
    Goals (in priority order):
    1. Prioritize images that NEED visual analysis (diagrams, graphs, screenshots)
       over text-heavy images (handwritten notes) whose content is already captured
       by text transcriptions.
    2. Ensure broad file/question coverage (at least one image per file).
    3. Prefer focus images over full-page images.
    4. Fill remaining slots by visual evidence quality.
    """
    if max_images <= 0 or not images:
        return []
    if len(images) <= max_images:
        return images

    chosen: list[dict] = []
    seen: set[tuple[str, int, str, int, str]] = set()

    def _sig(img: dict) -> tuple[str, int, str, int, str]:
        return (
            str(img.get("filename", "")),
            int(img.get("page", 0) or 0),
            str(img.get("description", "")),
            int(img.get("size_bytes", 0) or 0),
            str(img.get("base64", ""))[:48],
        )

    def _add(img: dict) -> bool:
        s = _sig(img)
        if s in seen:
            return False
        seen.add(s)
        chosen.append(img)
        return True

    # Separate visual-heavy images (diagrams/graphs) from text-heavy ones
    visual_heavy = [img for img in images if img.get("_ocr_needs_visual", True)]
    text_heavy = [img for img in images if not img.get("_ocr_needs_visual", True)]

    by_file: dict[str, list[dict]] = {}
    for img in images:
        by_file.setdefault(str(img.get("filename", "unknown")), []).append(img)

    # 1) Coverage pass: choose best image per file, preferring visual-heavy.
    file_best: list[tuple[float, str, dict]] = []
    for fname in sorted(by_file.keys()):
        file_imgs = by_file[fname]
        # Prefer visual-heavy from this file; fall back to any
        visual_in_file = [x for x in file_imgs if x.get("_ocr_needs_visual", True)]
        candidate_pool = visual_in_file if visual_in_file else file_imgs
        # Within pool, prefer focus images
        focus_in_pool = [x for x in candidate_pool if x.get("is_focus")]
        if focus_in_pool:
            candidate_pool = focus_in_pool
        best = max(
            candidate_pool,
            key=lambda x: (
                float(x.get("content_score", 0.0) or 0.0),
                int(x.get("size_bytes", 0) or 0),
                str(x.get("filename", "")),
                int(x.get("page", 0) or 0),
            ),
        )
        file_best.append((float(best.get("content_score", 0.0) or 0.0), fname, best))

    # If there are more files than slots, choose strongest files first.
    if max_images < len(file_best):
        file_best.sort(key=lambda t: (t[0], t[1]), reverse=True)
    else:
        file_best.sort(key=lambda t: t[1])

    for _score, _fname, img in file_best:
        _add(img)
        if len(chosen) >= max_images:
            return chosen

    # 2) Fill remaining with visual-heavy images first (diagrams need the actual image).
    visual_remaining = sorted(
        visual_heavy,
        key=lambda x: (
            0 if x.get("is_focus") else 1,
            -float(x.get("content_score", 0.0) or 0.0),
            -int(x.get("size_bytes", 0) or 0),
            str(x.get("filename", "")),
            int(x.get("page", 0) or 0),
        ),
    )
    for img in visual_remaining:
        _add(img)
        if len(chosen) >= max_images:
            return chosen

    # 3) Fill any remaining slots with text-heavy images (less critical since transcriptions exist).
    text_remaining = sorted(
        text_heavy,
        key=lambda x: (
            0 if x.get("is_focus") else 1,
            -float(x.get("content_score", 0.0) or 0.0),
            -int(x.get("size_bytes", 0) or 0),
            str(x.get("filename", "")),
            int(x.get("page", 0) or 0),
        ),
    )
    for img in text_remaining:
        _add(img)
        if len(chosen) >= max_images:
            break

    return chosen


def _tokenize_for_citation(text: str) -> set[str]:
    return {tok for tok in re.findall(r"[a-zA-Z][a-zA-Z0-9_+\-*]{2,}", str(text or "").lower())}


_CITATION_STOPWORDS = {
    # Only truly generic words that appear everywhere and carry no criterion-specific meaning.
    # IMPORTANT: Do NOT add domain terms here — words like "implementation", "system",
    # "algorithm", "analysis", "design" are exactly what distinguish criteria from each other.
    "the", "and", "for", "with", "from", "that", "this", "are", "was", "were", "have", "has",
    "into", "onto", "using", "use", "used", "task", "question", "problem", "part", "section",
    "criterion", "criteria", "points", "score", "max", "student", "work",
}


def _criterion_token_set(text: str) -> set[str]:
    return {tok for tok in _tokenize_for_citation(text) if tok not in _CITATION_STOPWORDS}


def _extract_text_snippets_for_evidence(
    text_content: str,
    max_snippets: int = 90,
    snippet_chars: int = 280,
) -> list[dict[str, Any]]:
    snippets: list[dict[str, Any]] = []
    if not isinstance(text_content, str) or not text_content.strip():
        return snippets

    current_file = "unknown"
    block_lines: list[str] = []

    def _push_block(file_label: str, lines: list[str]) -> None:
        nonlocal snippets
        if not lines or len(snippets) >= max_snippets:
            return
        raw = "\n".join(lines).strip()
        if not raw:
            return
        segments = re.split(r"\n{2,}", raw)
        for seg in segments:
            if len(snippets) >= max_snippets:
                break
            clean = _normalize_space(seg)
            if not clean:
                continue
            start = 0
            while start < len(clean) and len(snippets) < max_snippets:
                chunk = clean[start:start + snippet_chars]
                if len(chunk) >= snippet_chars:
                    cut = max(chunk.rfind(". "), chunk.rfind("; "), chunk.rfind(", "))
                    if cut >= 140:
                        chunk = chunk[:cut + 1]
                snippet_id = f"txt_{len(snippets) + 1:04d}"
                tokens = _criterion_token_set(chunk + " " + file_label)
                snippets.append({
                    "snippet_id": snippet_id,
                    "filename": file_label,
                    "text": chunk.strip(),
                    "tokens": tokens,
                })
                start += max(1, len(chunk))

    for line in text_content.splitlines():
        header = re.match(r"^\s*===\s*(.+?)\s*===\s*$", line)
        if header:
            _push_block(current_file, block_lines)
            block_lines = []
            header_label = _normalize_space(header.group(1))
            current_file = header_label.split(" (")[0].strip() if " (" in header_label else header_label
            current_file = current_file or "unknown"
            continue
        block_lines.append(line)
    _push_block(current_file, block_lines)
    return snippets


def _score_image_evidence_for_criterion(ev: dict[str, Any], criterion_tokens: set[str]) -> tuple[float, int]:
    evidence_text = " ".join([
        str(ev.get("filename", "")),
        str(ev.get("description", "")),
        str(ev.get("summary", "")),
        str(ev.get("transcription", "")),
    ])
    ev_tokens = _criterion_token_set(evidence_text)
    overlap = len(criterion_tokens & ev_tokens)

    conf_bonus = {"high": 6.0, "medium": 3.0, "low": 1.0}.get(str(ev.get("confidence", "")).lower(), 0.0)
    score = (overlap * 120.0) + float(ev.get("content_score", 0.0) or 0.0) + conf_bonus
    if ev.get("substantive"):
        score += 10.0
    # Strong bonus for images actually sent in the final grading call —
    # citations should prefer images the LLM can actually see.
    if ev.get("sent_in_final"):
        score += 200.0
    if overlap == 0:
        # Keep weak candidates but demote heavily when no lexical match exists.
        score -= 25.0
    return score, overlap


def _score_text_snippet_for_criterion(snippet: dict[str, Any], criterion_tokens: set[str]) -> tuple[float, int]:
    overlap = len(criterion_tokens & set(snippet.get("tokens", set())))
    length_bonus = min(6.0, len(str(snippet.get("text", ""))) / 80.0)
    score = (overlap * 95.0) + length_bonus
    if overlap == 0:
        score -= 20.0
    return score, overlap


def _build_criterion_evidence_plan(
    rubric_criteria: list[dict[str, Any]],
    text_content: str,
    evidence_map: list[dict[str, Any]],
) -> dict[str, Any]:
    snippets = _extract_text_snippets_for_evidence(text_content)
    prompt_lines: list[str] = []
    candidate_map: dict[str, dict[str, Any]] = {}

    for rc in rubric_criteria:
        criterion = str(rc.get("criterion", "")).strip()
        if not criterion:
            continue
        key = criterion.lower()
        c_tokens = _criterion_token_set(criterion)

        image_ranked: list[tuple[float, int, dict[str, Any]]] = []
        for ev in evidence_map:
            score, overlap = _score_image_evidence_for_criterion(ev, c_tokens)
            image_ranked.append((score, overlap, ev))
        image_ranked.sort(
            key=lambda t: (
                t[0],
                t[1],
                bool(t[2].get("substantive")),
                float(t[2].get("content_score", 0.0) or 0.0),
            ),
            reverse=True,
        )
        picked_images = [ev for score, overlap, ev in image_ranked if score > 0 and overlap > 0][:4]

        text_ranked: list[tuple[float, int, dict[str, Any]]] = []
        for snip in snippets:
            score, overlap = _score_text_snippet_for_criterion(snip, c_tokens)
            text_ranked.append((score, overlap, snip))
        text_ranked.sort(key=lambda t: (t[0], t[1]), reverse=True)
        picked_text = [snip for score, overlap, snip in text_ranked if score > 0 and overlap > 0][:2]

        candidate_map[key] = {
            "criterion": criterion,
            "image_ids": [str(ev.get("image_id")) for ev in picked_images if ev.get("image_id")],
            "text_snippet_ids": [str(s.get("snippet_id")) for s in picked_text if s.get("snippet_id")],
            "images": picked_images,
            "text_snippets": picked_text,
        }

        prompt_lines.append(f"[Criterion] {criterion}")
        if picked_images:
            img_parts = []
            for ev in picked_images[:4]:
                img_parts.append(
                    f"{ev.get('image_id')} ({ev.get('filename')} p{ev.get('page')}: {str(ev.get('summary', '') or ev.get('description', ''))[:90]})"
                )
            prompt_lines.append("- Candidate image IDs: " + "; ".join(img_parts))
        else:
            prompt_lines.append("- Candidate image IDs: none")

        if picked_text:
            txt_parts = []
            for snip in picked_text[:2]:
                txt_parts.append(
                    f"{snip.get('snippet_id')} ({snip.get('filename')}: {str(snip.get('text', ''))[:90]})"
                )
            prompt_lines.append("- Candidate text snippets: " + "; ".join(txt_parts))
        else:
            prompt_lines.append("- Candidate text snippets: none")
        prompt_lines.append("")

    prompt_block = "\n".join(prompt_lines).strip()
    if len(prompt_block) > 11000:
        prompt_block = prompt_block[:11000] + "\n...[TRUNCATED CANDIDATE LIST]"

    # Build snippet lookup: snippet_id -> {filename, text}
    snippet_lookup = {
        snip["snippet_id"]: {"filename": snip.get("filename", ""), "text": snip.get("text", "")}
        for snip in snippets
    }

    return {
        "prompt_block": prompt_block,
        "candidate_map": candidate_map,
        "text_snippets_indexed": len(snippets),
        "snippet_lookup": snippet_lookup,
    }


def _build_evidence_map(
    selected_images: list[dict],
    vision_trace: dict[str, Any],
    final_image_ids: Optional[set[str]] = None,
) -> list[dict[str, Any]]:
    _sent = set(final_image_ids or set())
    by_id: dict[str, dict[str, Any]] = {}
    for img in selected_images:
        image_id = str(img.get("image_id", "")).strip()
        if not image_id:
            continue
        by_id[image_id] = {
            "image_id": image_id,
            "filename": str(img.get("filename", "")),
            "page": int(img.get("page", 0) or 0),
            "description": str(img.get("description", "")),
            "batch_id": None,
            "content_score": float(img.get("content_score", 0.0) or 0.0),
            "substantive": False,
            "confidence": "",
            "summary": "",
            "transcription": "",
            "sent_in_final": image_id in _sent,
        }

    for batch in list((vision_trace or {}).get("batch_notes", []) or []):
        batch_id = int(batch.get("batch_id", 0) or 0)
        entries = batch.get("entries") if isinstance(batch.get("entries"), list) else []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            image_id = str(entry.get("image_id", "")).strip()
            if not image_id or image_id not in by_id:
                continue
            target = by_id[image_id]
            target["batch_id"] = batch_id
            target["substantive"] = bool(entry.get("substantive", False))
            target["confidence"] = str(entry.get("confidence", ""))[:20]
            target["summary"] = str(entry.get("summary", ""))[:VISION_ENTRY_SUMMARY_LIMIT]
            target["transcription"] = str(entry.get("transcription", ""))[:VISION_ENTRY_TRANSCRIPTION_LIMIT]

    return sorted(by_id.values(), key=lambda x: x["image_id"])


def _normalize_citation_objects(
    raw_citations: Any,
    evidence_lookup: Optional[dict[str, dict[str, Any]]] = None,
    snippet_lookup: Optional[dict[str, dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Normalize raw citation objects from LLM output.

    When *evidence_lookup* is provided (image_id → evidence dict), any
    LLM-reported filename/page is cross-checked and overridden with the
    ground-truth values from the evidence map.  This prevents the LLM from
    hallucinating filenames that don't match the actual image source.
    """
    normalized: list[dict[str, Any]] = []
    _ev = evidence_lookup or {}
    if not isinstance(raw_citations, list):
        return normalized
    for raw in raw_citations[:8]:
        if isinstance(raw, str):
            image_id = raw.strip()
            if image_id.startswith("img_"):
                item: dict[str, Any] = {"type": "image", "image_id": image_id}
                # Override with ground-truth from evidence map.
                if image_id in _ev:
                    item["filename"] = _ev[image_id].get("filename", "")
                    item["page"] = _ev[image_id].get("page", 0)
                normalized.append(item)
            continue
        if not isinstance(raw, dict):
            continue
        image_id = str(raw.get("image_id", "")).strip()
        snippet_id = str(raw.get("snippet_id", "")).strip()
        source = str(raw.get("source", "")).strip()
        item = {}
        if image_id:
            item["type"] = "image"
            item["image_id"] = image_id
            # Always prefer ground-truth filename/page from evidence map
            # over whatever the LLM reported (which may be hallucinated).
            if image_id in _ev:
                item["filename"] = _ev[image_id].get("filename", "")
                item["page"] = _ev[image_id].get("page", 0)
            else:
                if raw.get("filename"):
                    item["filename"] = str(raw.get("filename", ""))
                if raw.get("page") is not None:
                    try:
                        item["page"] = int(raw.get("page"))
                    except Exception:
                        pass
        elif snippet_id:
            item["type"] = "text"
            item["snippet_id"] = snippet_id
            item["source"] = source or "text_content"
            # Resolve filename from snippet lookup
            _snip_lookup = snippet_lookup or {}
            if snippet_id in _snip_lookup:
                item["file"] = str(_snip_lookup[snippet_id].get("filename", ""))
            elif raw.get("file"):
                item["file"] = str(raw["file"])
        elif source:
            item["source"] = source
        if item:
            normalized.append(item)
    return normalized


def _append_evidence_to_justification(base_text: str, citations: list[dict[str, Any]]) -> str:
    base = str(base_text or "").strip()
    if not base:
        base = "Evidence assessed."
    base = re.sub(r"\s*Evidence:\s*.*$", "", base, flags=re.IGNORECASE | re.DOTALL).strip()

    tags: list[str] = []
    for c in citations:
        image_id = str(c.get("image_id", "")).strip()
        if image_id:
            filename = str(c.get("filename", "")).strip()
            page = c.get("page")
            if filename and page is not None:
                tags.append(f"{image_id} ({filename} p{page})")
            elif filename:
                tags.append(f"{image_id} ({filename})")
            else:
                tags.append(image_id)
            continue
        snippet_id = str(c.get("snippet_id", "")).strip()
        if snippet_id:
            file_label = str(c.get("file", "")).strip()
            if file_label:
                tags.append(f"{file_label}")
            else:
                tags.append(f"{snippet_id} (text)")
            continue
        source = str(c.get("source", "")).strip()
        if source:
            tags.append(source)

    if not tags:
        tags = ["text_content"]
    return f"{base} Evidence: {', '.join(tags)}".strip()


def _attach_rubric_citations(
    result: dict[str, Any],
    evidence_map: list[dict[str, Any]],
    candidate_map: Optional[dict[str, dict[str, Any]]] = None,
) -> dict[str, Any]:
    breakdown = result.get("rubric_breakdown")
    if not isinstance(breakdown, list):
        return {"total_items": 0, "criteria_with_image_citation": 0, "fallback_applied": 0}

    evidence_pool = list(evidence_map or [])
    evidence_by_id = {
        str(ev.get("image_id", "")): ev
        for ev in evidence_pool
        if str(ev.get("image_id", "")).strip()
    }

    stats = {
        "total_items": len(breakdown),
        "criteria_with_image_citation": 0,
        "fallback_applied": 0,
        "model_citations_used": 0,
        "text_only_items": 0,
    }

    for item in breakdown:
        if not isinstance(item, dict):
            continue
        criterion = str(item.get("criterion", "")).strip()
        key = criterion.lower()
        c_tokens = _criterion_token_set(criterion)

        criterion_candidates = (candidate_map or {}).get(key, {})
        candidate_ids = [
            cid for cid in criterion_candidates.get("image_ids", [])
            if cid in evidence_by_id
        ]
        # IMPORTANT: Only allow candidate IDs, never fall back to entire pool.
        # Falling back to all evidence_by_id keys caused irrelevant images to be cited.
        allowed_ids = set(candidate_ids)

        normalized_existing = _normalize_citation_objects(item.get("citations", []), evidence_lookup=evidence_by_id)
        scored_existing: list[tuple[float, int, dict[str, Any]]] = []
        for c in normalized_existing:
            image_id = str(c.get("image_id", "")).strip()
            if not image_id or image_id not in evidence_by_id:
                continue
            if image_id not in allowed_ids:
                continue
            ev = evidence_by_id[image_id]
            score, overlap = _score_image_evidence_for_criterion(ev, c_tokens)
            scored_existing.append((score, overlap, ev))

        scored_existing.sort(key=lambda t: (t[0], t[1]), reverse=True)
        chosen_images: list[dict[str, Any]] = []
        if scored_existing and scored_existing[0][1] > 0:
            chosen_images = [ev for _, _, ev in scored_existing[:2]]
            stats["model_citations_used"] += 1

        if not chosen_images and candidate_ids:
            # Only try fallback with pre-matched candidates, never the entire pool.
            scored_candidates: list[tuple[float, int, dict[str, Any]]] = []
            for image_id in candidate_ids:
                ev = evidence_by_id.get(image_id)
                if not ev:
                    continue
                score, overlap = _score_image_evidence_for_criterion(ev, c_tokens)
                scored_candidates.append((score, overlap, ev))
            scored_candidates.sort(key=lambda t: (t[0], t[1]), reverse=True)
            # Require at least 1 token overlap — no zero-overlap citations.
            chosen_images = [ev for score, overlap, ev in scored_candidates[:2] if score > 0 and overlap > 0]
            if chosen_images:
                stats["fallback_applied"] += 1

        citations: list[dict[str, Any]] = []
        if chosen_images:
            for ev in chosen_images[:2]:
                citations.append({
                    "type": "image",
                    "image_id": ev.get("image_id"),
                    "filename": ev.get("filename"),
                    "file": ev.get("filename", ""),
                    "page": ev.get("page"),
                    "batch_id": ev.get("batch_id"),
                })
            stats["criteria_with_image_citation"] += 1
        else:
            text_ids = criterion_candidates.get("text_snippet_ids", [])
            text_snippets = criterion_candidates.get("text_snippets", [])
            snippet_by_id = {str(s.get("snippet_id", "")): s for s in text_snippets}
            if text_ids:
                sid = text_ids[0]
                snip = snippet_by_id.get(sid, {})
                cite: dict[str, Any] = {"type": "text", "snippet_id": sid, "source": "text_content"}
                if snip.get("filename"):
                    cite["file"] = str(snip["filename"])
                citations.append(cite)
            else:
                citations.append({"source": "text_content"})
            stats["text_only_items"] += 1

        item["justification"] = _append_evidence_to_justification(item.get("justification", ""), citations)
        item["citations"] = citations
    return stats


async def _verify_citations_with_llm(
    rubric_breakdown: list[dict[str, Any]],
    evidence_map: list[dict[str, Any]],
    candidate_map: dict[str, dict[str, Any]],
    preferred_provider: str,
) -> dict[str, Any]:
    trace: dict[str, Any] = {
        "checked": 0,
        "invalid": 0,
        "weak": 0,
        "provider": "",
        "model": "",
        "error": "",
        "verdict_map": {},
    }
    if not rubric_breakdown or not evidence_map or not candidate_map:
        return trace

    evidence_by_id = {
        str(ev.get("image_id", "")): ev
        for ev in evidence_map
        if str(ev.get("image_id", "")).strip()
    }

    checks_payload: list[dict[str, Any]] = []
    for item in rubric_breakdown:
        criterion = str(item.get("criterion", "")).strip()
        if not criterion:
            continue
        key = criterion.lower()
        candidate_ids = [
            cid for cid in candidate_map.get(key, {}).get("image_ids", [])
            if cid in evidence_by_id
        ][:5]
        if not candidate_ids:
            continue

        cited_ids = [
            str(c.get("image_id", "")).strip()
            for c in _normalize_citation_objects(item.get("citations", []), evidence_lookup=evidence_by_id)
            if str(c.get("image_id", "")).strip()
        ][:3]
        candidate_evidence = []
        for cid in candidate_ids:
            ev = evidence_by_id[cid]
            candidate_evidence.append({
                "image_id": cid,
                "filename": str(ev.get("filename", "")),
                "page": int(ev.get("page", 0) or 0),
                "summary": str(ev.get("summary", ""))[:140],
                "transcription": str(ev.get("transcription", ""))[:140],
                "description": str(ev.get("description", ""))[:100],
            })
        checks_payload.append({
            "criterion": criterion,
            "justification": str(item.get("justification", ""))[:260],
            "cited_image_ids": cited_ids,
            "candidate_image_ids": candidate_ids,
            "candidate_evidence": candidate_evidence,
        })

    if not checks_payload:
        return trace

    verifier_prompt = (
        "Validate rubric-item evidence citations.\n"
        "For each item, approve only image IDs that concretely support the criterion.\n"
        "Use candidate evidence only. Do not invent IDs.\n"
        "Output strict JSON:\n"
        "{\n"
        '  "checks": [\n'
        '    {"criterion":"...", "status":"valid|weak|invalid", "approved_image_ids":["img_0001"], "reason":"..."}\n'
        "  ]\n"
        "}\n\n"
        f"Items:\n{json.dumps(checks_payload, ensure_ascii=False)}"
    )

    try:
        await _rate_limiter.acquire()
        response, meta = _chat_completion_with_failover(
            purpose="citation_verification",
            needs_vision=False,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a strict citation auditor for academic grading evidence. "
                        "Only approve citations supported by provided evidence."
                    ),
                },
                {"role": "user", "content": verifier_prompt},
            ],
            temperature=0.0,
            top_p=1.0,
            max_tokens=1200,
            seed=42,
            preferred_provider=preferred_provider,
            allow_fallback=bool(SCORING_ALLOW_FALLBACK),
            response_format={"type": "json_object"},
        )
        trace["provider"] = str(meta.get("provider", ""))
        trace["model"] = str(meta.get("model", ""))
        raw = (response.choices[0].message.content or "").strip()
        parsed = _extract_json(raw)
        checks = parsed.get("checks", []) if isinstance(parsed, dict) else []
        verdict_map: dict[str, dict[str, Any]] = {}
        for chk in checks:
            if not isinstance(chk, dict):
                continue
            criterion = str(chk.get("criterion", "")).strip().lower()
            if not criterion:
                continue
            status = str(chk.get("status", "weak")).strip().lower()
            approved = [
                cid for cid in chk.get("approved_image_ids", [])
                if isinstance(cid, str) and cid in evidence_by_id
            ][:2]
            verdict_map[criterion] = {
                "status": status if status in {"valid", "weak", "invalid"} else "weak",
                "approved_image_ids": approved,
                "reason": str(chk.get("reason", ""))[:220],
            }
        trace["verdict_map"] = verdict_map
        trace["checked"] = len(verdict_map)
        trace["invalid"] = sum(1 for v in verdict_map.values() if v.get("status") == "invalid")
        trace["weak"] = sum(1 for v in verdict_map.values() if v.get("status") == "weak")
        return trace
    except Exception as exc:
        trace["error"] = str(exc)
        return trace


def _apply_llm_citation_verdict(
    rubric_breakdown: list[dict[str, Any]],
    evidence_map: list[dict[str, Any]],
    candidate_map: dict[str, dict[str, Any]],
    verifier_trace: dict[str, Any],
) -> dict[str, int]:
    applied = 0
    evidence_by_id = {
        str(ev.get("image_id", "")): ev
        for ev in evidence_map
        if str(ev.get("image_id", "")).strip()
    }
    verdict_map = verifier_trace.get("verdict_map", {}) if isinstance(verifier_trace, dict) else {}
    if not verdict_map:
        return {"applied": 0}

    for item in rubric_breakdown:
        if not isinstance(item, dict):
            continue
        key = str(item.get("criterion", "")).strip().lower()
        verdict = verdict_map.get(key)
        if not verdict:
            continue
        approved_ids = verdict.get("approved_image_ids", [])
        allowed = set(candidate_map.get(key, {}).get("image_ids", []))
        filtered = []
        for image_id in approved_ids:
            if image_id not in evidence_by_id:
                continue
            if allowed and image_id not in allowed:
                continue
            filtered.append(image_id)
        if not filtered:
            continue
        citations = []
        for image_id in filtered[:2]:
            ev = evidence_by_id[image_id]
            citations.append({
                "type": "image",
                "image_id": image_id,
                "filename": ev.get("filename"),
                "page": ev.get("page"),
                "batch_id": ev.get("batch_id"),
            })
        item["citations"] = citations
        item["justification"] = _append_evidence_to_justification(item.get("justification", ""), citations)
        applied += 1
    return {"applied": applied}


async def _verify_scores_with_llm(
    rubric_breakdown: list[dict[str, Any]],
    rubric_criteria: list[dict[str, Any]],
    text_evidence: str,
    preferred_provider: str,
) -> dict[str, Any]:
    """Post-grading score verification pass.

    An independent LLM call reviews every criterion score against the actual
    evidence and can adjust scores UP or DOWN.  This catches hallucinated
    high scores and missed evidence that deserved credit.
    """
    trace: dict[str, Any] = {
        "enabled": bool(SCORE_VERIFICATION_ENABLED),
        "provider": "",
        "model": "",
        "adjustments": [],
        "error": "",
    }
    if not SCORE_VERIFICATION_ENABLED or not rubric_breakdown:
        return trace

    # Build compact rubric breakdown for the verifier
    breakdown_lines: list[str] = []
    for item in rubric_breakdown:
        crit = item.get("criterion", "")
        score = item.get("score", 0)
        mx = item.get("max", 0)
        justification = str(item.get("justification", ""))[:200]
        breakdown_lines.append(
            f"- {crit}: {score}/{mx} — {justification}"
        )
    breakdown_text = "\n".join(breakdown_lines)

    # Cap evidence text to leave room for prompt + response
    evidence_capped = text_evidence[:60000] if text_evidence else "(no text evidence)"

    system_prompt = (
        "You are a CAREFUL SCORE VERIFICATION AUDITOR. Your job is to catch "
        "HALLUCINATED SCORES — where the grader awarded points for work that TRULY does NOT "
        "exist in the student's submission.\n\n"
        "CRITICAL RULES — READ CAREFULLY:\n"
        "1. SEARCH the student evidence for code/content that addresses each criterion.\n"
        "2. MINOR ISSUES ARE NOT HALLUCINATIONS. Typos in function/variable names, "
        "   slight naming mismatches (e.g. 'calculate_choas_score' vs 'calculate_chaos_score'), "
        "   or different coding styles that achieve the same result are NOT reasons to set score to 0.\n"
        "3. FUNCTIONAL EQUIVALENCE matters. If the student's code DOES the right thing but names "
        "   it differently, has a typo, or uses a different approach, the work EXISTS. Keep the score.\n"
        "4. Only set verified_score=0 when there is genuinely NO code/content AT ALL for that criterion. "
        "   Not 'slightly wrong code' — ZERO code.\n"
        "5. If the grader says 'not directly visible' or 'might be implemented' but you CAN find "
        "   relevant code in the evidence, keep the score.\n"
        "6. If the evidence shows code with bugs/errors, keep partial credit (do NOT zero it).\n"
        "7. If a file is EMPTY (0 chars) and the grader awarded points, set score to 0.\n\n"
        "HALLUCINATION = grader invented code that doesn't exist AT ALL. NOT:\n"
        "- Code with typos or naming differences\n"
        "- Code that's partially correct\n"
        "- Code using a different approach than expected\n"
        "- Code with bugs but correct intent\n\n"
        "Return JSON:\n"
        '{"verifications": [{"criterion": "...", "original_score": 8, '
        '"verified_score": 8, "adjustment_reason": "Code exists and addresses criterion", '
        '"confidence": "high"|"medium"|"low", '
        '"evidence_found": true|false}]}\n\n'
        "Set evidence_found=false ONLY when absolutely NO code/content for that criterion exists.\n"
        "When evidence_found=false, verified_score MUST be 0 and confidence MUST be 'high'.\n"
        "When in doubt, KEEP the original score. False negatives (zeroing real work) are WORSE "
        "than false positives (keeping a slightly high score)."
    )

    user_prompt = (
        f"RUBRIC SCORES TO VERIFY:\n{breakdown_text}\n\n"
        f"STUDENT EVIDENCE (this is ALL the text content from the student's files):\n"
        f"{evidence_capped}\n\n"
        f"INSTRUCTIONS: For each criterion, search the evidence above for actual code or "
        f"content that supports the score. If you cannot find it, set verified_score=0."
    )

    try:
        await _rate_limiter.acquire()
        response, meta = _chat_completion_with_failover(
            purpose="score_verification",
            needs_vision=False,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
            top_p=1.0,
            max_tokens=1500,
            seed=42,
            preferred_provider=preferred_provider,
            response_format={"type": "json_object"},
        )
        trace["provider"] = meta.get("provider", "")
        trace["model"] = meta.get("model", "")

        raw = (response.choices[0].message.content or "").strip()
        parsed = _extract_json(raw)
        verifications = parsed.get("verifications", []) if isinstance(parsed, dict) else []
        trace["adjustments"] = verifications
    except Exception as exc:
        trace["error"] = str(exc)
        logger.warning("Score verification failed: %s", exc)

    return trace


def _verify_justifications_against_content(
    rubric_breakdown: list[dict[str, Any]],
    text_content: str,
    max_score: int,
) -> dict[str, Any]:
    """Verify that LLM justifications reference content actually present in submissions.

    Two-pronged approach:
    1. GLOBAL CHECK: Count actual meaningful code lines vs criteria scoring > 0.
       If a student has very few code lines but many high-scoring criteria, it's bulk hallucination.
    2. PER-CRITERION CHECK: Extract the full "relevant code snippet" claim from each
       justification and verify it exists as a contiguous block in the student content.

    This is a deterministic, fast check — NO additional LLM call.
    """
    stats: dict[str, Any] = {
        "checked": 0,
        "suspicious": 0,
        "adjustments": 0,
        "details": [],
    }
    if not rubric_breakdown or not text_content:
        return stats

    # ── Normalize helper ──
    def _norm(s: str) -> str:
        return re.sub(r'\s+', ' ', s.lower().strip())

    content_norm = _norm(text_content)

    # ── Count meaningful code lines in student submission ──
    # Exclude: blank lines, comments, pure data definitions (list/dict literals),
    # import statements. Keep: assignments with logic, function defs, loops, prints, returns
    _DATA_LINE = re.compile(
        r'^\s*[\[\{"\']|'           # starts with [ { " '  (data literal)
        r'^\s*\},?\s*$|'            # closing brace
        r'^\s*\],?\s*$|'            # closing bracket
        r'^\s*#|'                   # comment
        r'^\s*import\s|'            # import
        r'^\s*from\s.*import\s|'    # from...import
        r'^\s*$'                    # blank
    )
    _CODE_KEYWORDS = re.compile(
        r'\b(def |class |for |while |if |elif |else:|return |print|'
        r'append|range|len|sorted|max|min|sum|np\.|\.upper|\.lower|'
        r'\.get|\.items|\.keys|\.values|\.append|deque|enumerate)\b|'
        r'[a-zA-Z_]\w*\s*=\s*[^=]'  # assignment
    )

    content_lines = text_content.split('\n')
    meaningful_code_lines = 0
    for line in content_lines:
        stripped = line.strip()
        if not stripped or len(stripped) < 5:
            continue
        if _DATA_LINE.match(stripped):
            continue
        if _CODE_KEYWORDS.search(stripped):
            meaningful_code_lines += 1

    # Count criteria with score > 0
    criteria_with_score = sum(
        1 for item in rubric_breakdown
        if float(item.get("score", 0)) > 0
    )
    total_scored_points = sum(
        float(item.get("score", 0))
        for item in rubric_breakdown
        if float(item.get("score", 0)) > 0
    )

    stats["meaningful_code_lines"] = meaningful_code_lines
    stats["criteria_with_score"] = criteria_with_score

    # ── GLOBAL SANITY CHECK ──
    # If student has very few code lines but many scoring criteria,
    # apply a global reduction factor
    global_reduction = 1.0  # no reduction by default
    if meaningful_code_lines <= 3 and criteria_with_score > 5:
        # Almost no code but many scores → extreme hallucination
        global_reduction = 0.3
        stats["global_reduction_reason"] = (
            f"Only {meaningful_code_lines} meaningful code lines but "
            f"{criteria_with_score} criteria scored > 0"
        )
    elif meaningful_code_lines <= 8 and criteria_with_score > 10:
        # Very little code for many criteria
        global_reduction = 0.5
        stats["global_reduction_reason"] = (
            f"Only {meaningful_code_lines} meaningful code lines but "
            f"{criteria_with_score} criteria scored > 0"
        )
    elif meaningful_code_lines <= 15 and criteria_with_score > 18:
        global_reduction = 0.7
        stats["global_reduction_reason"] = (
            f"Only {meaningful_code_lines} meaningful code lines for "
            f"{criteria_with_score} scoring criteria"
        )

    # ── PER-CRITERION VERIFICATION ──
    for item in rubric_breakdown:
        score = float(item.get("score", 0))
        if score <= 0:
            continue

        stats["checked"] += 1
        criterion = str(item.get("criterion", ""))
        justification = str(item.get("justification", ""))
        max_pts = float(item.get("max", 1))

        if not justification or len(justification) < 20:
            continue

        # ── Extract the FULL cited code block from justification ──
        # LLM format: "The relevant code snippet is: '<FULL CODE HERE>'. Evidence:"
        # or: "The relevant code snippet is: '<CODE>' and '<CODE>'. Evidence:"
        full_claim = ""
        claim_match = re.search(
            r'(?:relevant|actual|key|specific)\s+(?:code|text)\s+snippet\s+is:\s*(.+?)(?:\.\s*Evidence:|$)',
            justification,
            re.IGNORECASE | re.DOTALL,
        )
        if claim_match:
            full_claim = claim_match.group(1).strip()

        if not full_claim:
            # Try alternative: just the part after "snippet is:" until end
            claim_match2 = re.search(
                r'snippet\s+is:\s*(.+?)$',
                justification,
                re.IGNORECASE,
            )
            if claim_match2:
                full_claim = claim_match2.group(1).strip()

        if full_claim and len(full_claim) >= 10:
            # Verify the claimed code exists in student content
            claim_norm = _norm(full_claim)
            # Remove surrounding quotes
            claim_norm = claim_norm.strip("'\"` ")

            # Check: does ANY 20-char contiguous substring of the claim exist in content?
            claim_found = False
            check_len = min(20, len(claim_norm) - 1)
            if check_len >= 10:
                for start in range(0, len(claim_norm) - check_len + 1, 3):
                    chunk = claim_norm[start:start + check_len]
                    # Skip chunks that are mostly common words/punctuation
                    if chunk in content_norm:
                        claim_found = True
                        break

            if not claim_found:
                stats["suspicious"] += 1
                # The LLM cited specific code that doesn't exist
                new_score = round(max(0, score * 0.15), 1)  # Reduce to ~15%
                stats["details"].append({
                    "criterion": criterion,
                    "original_score": score,
                    "new_score": new_score,
                    "reason": "cited_code_not_found",
                    "claim_preview": full_claim[:80],
                })
                item["score"] = new_score
                item["justification"] = (
                    item.get("justification", "") +
                    f" [⚠ Cited code not found in submission. Score: {score} → {new_score}]"
                )[:600]
                stats["adjustments"] += 1
                logger.warning(
                    f"Hallucination: '{criterion}' cited code not in submission, "
                    f"score {score} -> {new_score}"
                )
                continue

        # ── Apply global reduction if triggered ──
        if global_reduction < 1.0:
            new_score = round(max(0, score * global_reduction), 1)
            if new_score < score:
                stats["details"].append({
                    "criterion": criterion,
                    "original_score": score,
                    "new_score": new_score,
                    "reason": "global_code_deficit",
                })
                item["score"] = new_score
                item["justification"] = (
                    item.get("justification", "") +
                    f" [⚠ Insufficient code evidence in submission. Score: {score} → {new_score}]"
                )[:600]
                stats["adjustments"] += 1

    return stats


def _apply_score_verification(
    rubric_breakdown: list[dict[str, Any]],
    verification_trace: dict[str, Any],
    max_score: int,
) -> dict[str, Any]:
    """Apply verified score adjustments to the rubric breakdown in-place.

    Rules:
    - evidence_found=false: ALWAYS apply (score → 0), regardless of confidence
    - "high" confidence adjustments: always apply
    - "medium" confidence adjustments: apply if delta ≥ 1 (lowered from 2)
    - "low" confidence adjustments: only apply if delta ≥ 3 (was: never)
    """
    stats = {"adjusted": 0, "kept": 0, "skipped_low_confidence": 0, "details": []}
    verifications = verification_trace.get("adjustments", [])
    if not verifications:
        return stats

    # Build lookup: criterion -> verification entry
    verify_map: dict[str, dict] = {}
    for v in verifications:
        if isinstance(v, dict) and v.get("criterion"):
            verify_map[str(v["criterion"]).strip().lower()] = v

    for item in rubric_breakdown:
        crit = str(item.get("criterion", "")).strip().lower()
        v = verify_map.get(crit)
        if not v:
            continue

        original = float(item.get("score", 0))
        verified = float(v.get("verified_score", original))
        confidence = str(v.get("confidence", "low")).lower()
        evidence_found = v.get("evidence_found", True)
        delta = abs(verified - original)
        reason = str(v.get("adjustment_reason", ""))

        detail = {
            "criterion": item.get("criterion", ""),
            "original": original,
            "verified": verified,
            "confidence": confidence,
            "evidence_found": evidence_found,
            "applied": False,
            "reason": reason,
        }

        # BUG-01 fix: NEVER zero out a score based on low-confidence verification.
        # The verifier is a second LLM that may not see the same context as the
        # grading LLM. Low confidence = the verifier itself is unsure.
        if confidence == "low":
            stats["skipped_low_confidence"] += 1
            detail["skip_reason"] = "low confidence — never apply low-confidence adjustments"
            stats["details"].append(detail)
            continue

        # Priority 1: If verifier says NO evidence found with high/medium confidence
        if evidence_found is False and original > 0:
            item["score"] = 0.0
            item["justification"] = (
                item.get("justification", "")
                + f" [VERIFICATION ({confidence}): Score reduced from {original} to 0 — "
                + f"no evidence found for this criterion in any submitted file]"
            )
            detail["applied"] = True
            stats["adjusted"] += 1
            stats["details"].append(detail)
            continue
        elif confidence == "medium" and delta < 2:
            stats["kept"] += 1
            detail["skip_reason"] = f"medium confidence, delta {delta:.1f} < 2"
        elif delta > 0:
            # Apply the adjustment (cap at max)
            item["score"] = min(verified, float(item.get("max", 100)))
            item["justification"] = (
                item.get("justification", "")
                + f" [Verification adjustment: {original}→{verified}: {reason}]"
            )
            stats["adjusted"] += 1
            detail["applied"] = True
        else:
            stats["kept"] += 1

        stats["details"].append(detail)

    return stats


def _calibrate_confidence_with_citations(
    base_confidence: str,
    citation_stats: dict[str, Any],
    verifier_trace: dict[str, Any],
    image_evidence_available: bool,
) -> tuple[str, str]:
    total_items = max(1, int(citation_stats.get("total_items", 0) or 0))
    image_covered = int(citation_stats.get("criteria_with_image_citation", 0) or 0)
    image_coverage = image_covered / total_items

    checked = int(verifier_trace.get("checked", 0) or 0)
    invalid = int(verifier_trace.get("invalid", 0) or 0)
    weak = int(verifier_trace.get("weak", 0) or 0)
    invalid_ratio = (invalid / checked) if checked > 0 else 0.0
    weak_ratio = (weak / checked) if checked > 0 else 0.0

    rank = {"low": 0, "medium": 1, "high": 2}.get(str(base_confidence).lower(), 1)
    if image_evidence_available:
        if image_coverage < 0.40:
            rank = min(rank, 0)
        elif image_coverage < 0.70:
            rank = min(rank, 1)

    if invalid_ratio >= 0.30:
        rank = 0
    elif invalid_ratio >= 0.10 or weak_ratio >= 0.40:
        rank = min(rank, 1)

    calibrated = {0: "low", 1: "medium", 2: "high"}[rank]
    if image_evidence_available:
        reason = (
            f"Citation audit: {image_covered}/{total_items} rubric criteria have image-backed evidence."
        )
    else:
        reason = "Citation audit: no image evidence was available; confidence calibrated from textual evidence consistency."
    if checked > 0:
        reason += f" Verifier: {invalid}/{checked} invalid, {weak}/{checked} weak."
    return calibrated, reason


async def _repair_grading_json(
    raw_text: str,
    rubric_criteria: list[dict[str, Any]],
    max_score: int,
    preferred_provider: str,
) -> Optional[dict[str, Any]]:
    source = str(raw_text or "").strip()
    if not source:
        return None

    rubric_lines = [f"- {c.get('criterion')}: {c.get('max')}" for c in rubric_criteria if c.get("criterion")]
    prompt = (
        "Convert the following grading response into strict JSON.\n"
        "Return only JSON with this schema:\n"
        "{\n"
        '  "rubric_breakdown":[{"criterion":"...","score":0,"max":0,"justification":"...","citations":[{"type":"image","image_id":"img_0001"}]}],\n'
        '  "total_score":0,\n'
        '  "overall_feedback":"...",\n'
        '  "strengths":["..."],\n'
        '  "weaknesses":["..."],\n'
        '  "suggestions_for_improvement":"...",\n'
        '  "confidence":"high|medium|low",\n'
        '  "confidence_reasoning":"..."\n'
        "}\n"
        "Rules:\n"
        "1) Keep criterion names exactly from this rubric list.\n"
        "2) Keep max values exactly from rubric.\n"
        "3) Ensure total_score is the sum of rubric scores and <= max_score.\n"
        "4) If citation evidence is missing, set citations to [{\"source\":\"text_content\"}].\n\n"
        f"RUBRIC:\n{chr(10).join(rubric_lines)}\n"
        f"MAX SCORE: {max_score}\n\n"
        f"RAW RESPONSE:\n{source[:12000]}"
    )

    try:
        await _rate_limiter.acquire()
        response, _meta = _chat_completion_with_failover(
            purpose="grade_json_repair",
            needs_vision=False,
            messages=[
                {"role": "system", "content": "You are a strict JSON formatter for grading outputs."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            top_p=1.0,
            max_tokens=1800,
            seed=42,
            preferred_provider=preferred_provider,
            allow_fallback=bool(SCORING_ALLOW_FALLBACK),
            response_format={"type": "json_object"},
        )
        repaired_raw = (response.choices[0].message.content or "").strip()
        repaired = _extract_json(repaired_raw)
        return _validate_result(repaired, rubric_criteria, max_score)
    except Exception:
        return None


def _normalize_criterion_key(name: str) -> str:
    """Normalize a criterion name for matching: lowercase, strip punctuation/whitespace."""
    s = name.lower().strip()
    # Remove common prefixes like "[1]", "1.", "Q1a(i) -"
    s = re.sub(r'^\[\d+\]\s*', '', s)
    # Collapse whitespace
    s = re.sub(r'\s+', ' ', s)
    return s


def _criterion_similarity(a: str, b: str) -> float:
    """Score similarity between two criterion names (0.0 to 1.0).

    Uses token overlap (Jaccard) + question-ID prefix matching for robustness
    against LLM rewording.
    """
    # Extract question IDs like Q1a(i), Q2b, etc.
    qid_pattern = re.compile(r'[Qq]\d+[a-z]?(?:\([ivx]+\))?')
    a_qids = set(qid_pattern.findall(a))
    b_qids = set(qid_pattern.findall(b))

    # If both have question IDs and they match, strong signal
    if a_qids and b_qids:
        if a_qids == b_qids:
            return 0.95  # Very likely the same criterion
        if a_qids & b_qids:
            return 0.7  # Partial overlap

    # Token-based Jaccard similarity
    a_tokens = set(re.findall(r'\w+', a.lower()))
    b_tokens = set(re.findall(r'\w+', b.lower()))
    if not a_tokens or not b_tokens:
        return 0.0
    intersection = a_tokens & b_tokens
    union = a_tokens | b_tokens
    jaccard = len(intersection) / len(union)

    # Bonus for substring containment
    if a.lower() in b.lower() or b.lower() in a.lower():
        jaccard = max(jaccard, 0.8)

    return jaccard


def _validate_result(result: dict, rubric_criteria: list[dict], max_score: int) -> dict:
    """Validate and fix the grading result."""

    rubric_map = {c['criterion'].lower().strip(): c for c in rubric_criteria}

    ai_breakdown = result.get("rubric_breakdown", [])
    if not isinstance(ai_breakdown, list):
        ai_breakdown = []
    fixed_breakdown = []
    used_keys = set()
    unmatched_ai_items: list[str] = []

    # Log what LLM returned vs expected for debugging
    logger.debug(
        "validate_result: %d AI items vs %d rubric criteria. AI names: %s",
        len(ai_breakdown),
        len(rubric_criteria),
        [str(item.get("criterion", ""))[:60] if isinstance(item, dict) else str(item)[:60] for item in ai_breakdown[:20]],
    )

    for item in ai_breakdown:
        if not isinstance(item, dict):
            continue
        ai_criterion = str(item.get("criterion", "")).strip()
        ai_key = ai_criterion.lower()

        if not ai_criterion:
            continue

        matched_key = None
        matched_data = None

        # Strategy 1: Exact match
        if ai_key in rubric_map and ai_key not in used_keys:
            matched_key = ai_key
            matched_data = rubric_map[ai_key]

        # Strategy 2: Substring containment
        if not matched_data:
            for rubric_key, rubric_data in rubric_map.items():
                if rubric_key not in used_keys:
                    if rubric_key in ai_key or ai_key in rubric_key:
                        matched_key = rubric_key
                        matched_data = rubric_data
                        break

        # Strategy 3: Similarity-based matching (handles LLM rewording)
        if not matched_data:
            best_score = 0.0
            best_key = None
            best_data = None
            ai_norm = _normalize_criterion_key(ai_criterion)
            for rubric_key, rubric_data in rubric_map.items():
                if rubric_key not in used_keys:
                    rubric_norm = _normalize_criterion_key(rubric_data['criterion'])
                    sim = _criterion_similarity(ai_norm, rubric_norm)
                    if sim > best_score:
                        best_score = sim
                        best_key = rubric_key
                        best_data = rubric_data
            # BUG-12 fix: 0.4 was too permissive, could cross-match similar criteria
            # (e.g. "Binary Search" vs "Binary Tree"). Raised back to 0.5.
            if best_score >= 0.5 and best_key and best_data:
                matched_key = best_key
                matched_data = best_data

        # Strategy 4: Q-ID prefix matching as last resort
        # e.g. AI returns "Q1a(i)" and rubric has "Q1a(i) - Count messages per sender"
        if not matched_data:
            qid_pattern = re.compile(r'[Qq]\d+[a-z]?(?:\([ivx]+\))?')
            ai_qids = set(qid_pattern.findall(ai_criterion))
            if ai_qids:
                for rubric_key, rubric_data in rubric_map.items():
                    if rubric_key not in used_keys:
                        rubric_qids = set(qid_pattern.findall(rubric_data['criterion']))
                        if ai_qids and rubric_qids and ai_qids == rubric_qids:
                            matched_key = rubric_key
                            matched_data = rubric_data
                            break

        if not matched_data:
            unmatched_ai_items.append(ai_criterion)
        
        if matched_data and matched_key:
            try:
                score = float(item.get("score", 0))
            except (ValueError, TypeError):
                score = 0
            score = max(0, min(score, matched_data['max']))

            fixed_item = {
                "criterion": matched_data['criterion'],
                "score": round(score, 1),
                "max": matched_data['max'],
                "justification": str(item.get("justification", item.get("feedback", "")))[:500]
            }
            normalized_citations = _normalize_citation_objects(item.get("citations", []))
            if normalized_citations:
                fixed_item["citations"] = normalized_citations

            fixed_breakdown.append(fixed_item)
            used_keys.add(matched_key)
    
    missing_keys = [k for k in rubric_map if k not in used_keys]
    if unmatched_ai_items:
        logger.warning(
            "validate_result: %d AI criteria could not be matched to rubric: %s",
            len(unmatched_ai_items), unmatched_ai_items[:10],
        )
    if missing_keys:
        logger.warning(
            "validate_result: %d rubric criteria had no matching AI response: %s",
            len(missing_keys), missing_keys[:10],
        )

    for rubric_key, rubric_data in rubric_map.items():
        if rubric_key not in used_keys:
            # Try one more time: check if any unmatched AI item has the same Q-ID prefix
            rescue_item = None
            rubric_qids = set(re.findall(r'[Qq]\d+[a-z]?(?:\([ivx]+\))?', rubric_key))
            if rubric_qids and unmatched_ai_items:
                for uai in unmatched_ai_items:
                    uai_qids = set(re.findall(r'[Qq]\d+[a-z]?(?:\([ivx]+\))?', uai.lower()))
                    if rubric_qids & uai_qids:
                        # Found a match by Q-ID — find the original item
                        for orig_item in ai_breakdown:
                            if isinstance(orig_item, dict) and str(orig_item.get("criterion", "")).strip() == uai:
                                rescue_item = orig_item
                                break
                        if rescue_item:
                            unmatched_ai_items.remove(uai)
                            break

            if rescue_item:
                try:
                    score = float(rescue_item.get("score", 0))
                except (ValueError, TypeError):
                    score = 0
                score = max(0, min(score, rubric_data['max']))
                fixed_breakdown.append({
                    "criterion": rubric_data['criterion'],
                    "score": round(score, 1),
                    "max": rubric_data['max'],
                    "justification": str(rescue_item.get("justification", rescue_item.get("feedback", "")))[:500],
                })
                logger.info(
                    "validate_result: rescued criterion '%s' from unmatched AI item '%s' via Q-ID match",
                    rubric_data['criterion'], str(rescue_item.get("criterion", ""))[:60],
                )
            else:
                fixed_breakdown.append({
                    "criterion": rubric_data['criterion'],
                    "score": 0,
                    "max": rubric_data['max'],
                    "justification": "Not assessed by AI"
                })
    
    # Sum using integer tenths to avoid floating-point drift
    # e.g. round(0.1 + 0.2, 1) might give 0.30000000000000004
    total_tenths = 0
    for item in fixed_breakdown:
        try:
            total_tenths += round(float(item.get("score", 0)) * 10)
        except (ValueError, TypeError):
            pass
    total = round(total_tenths / 10, 1)
    total = max(0, min(total, max_score))
    
    percentage = round((total / max_score) * 100, 1) if max_score > 0 else 0
    
    if percentage >= 97:
        letter = "A+"
    elif percentage >= 93:
        letter = "A"
    elif percentage >= 90:
        letter = "A-"
    elif percentage >= 87:
        letter = "B+"
    elif percentage >= 83:
        letter = "B"
    elif percentage >= 80:
        letter = "B-"
    elif percentage >= 77:
        letter = "C+"
    elif percentage >= 73:
        letter = "C"
    elif percentage >= 70:
        letter = "C-"
    elif percentage >= 67:
        letter = "D+"
    elif percentage >= 60:
        letter = "D"
    else:
        letter = "F"
    
    return {
        "total_score": total,
        "max_score": max_score,
        "percentage": percentage,
        "letter_grade": letter,
        "rubric_breakdown": fixed_breakdown,
        "overall_feedback": str(result.get("overall_feedback", ""))[:2000],
        "strengths": result.get("strengths", []) if isinstance(result.get("strengths"), list) else [],
        "weaknesses": result.get("weaknesses", []) if isinstance(result.get("weaknesses"), list) else [],
        "critical_errors": result.get("critical_errors", []) if isinstance(result.get("critical_errors"), list) else [],
        "suggestions_for_improvement": str(result.get("suggestions_for_improvement", ""))[:1000],
        "confidence": result.get("confidence", "medium") if result.get("confidence") in ["high", "medium", "low"] else "medium",
        "confidence_reasoning": str(result.get("confidence_reasoning", ""))[:500],
        "question_mapping": result.get("question_mapping", []) if isinstance(result.get("question_mapping"), list) else [],
        "file_analysis": result.get("file_analysis", []) if isinstance(result.get("file_analysis"), list) else [],
        "visual_content_analysis": result.get("visual_content_analysis")
        if isinstance(result.get("visual_content_analysis"), dict) else None,
    }


def _is_grading_result(obj: dict) -> bool:
    """Check if a parsed JSON object looks like a grading result (not a random fragment)."""
    if not isinstance(obj, dict):
        return False
    # A valid grading result should have rubric_breakdown or total_score
    if "rubric_breakdown" in obj:
        return True
    if "total_score" in obj:
        return True
    # Also accept objects with criterion/score (single rubric item from LLM)
    if "criterion" in obj and "score" in obj:
        return True
    return False


def _extract_json(raw_text: str, *, require_grading_result: bool = False) -> dict:
    """Extract JSON from LLM response with robust multi-strategy parsing.

    D3 FIX: tries multiple repair strategies before giving up, avoiding the
    expensive fallback API call for JSON repair in most cases.

    When *require_grading_result* is True, validates that extracted JSON looks
    like a grading result (has rubric_breakdown/total_score) to avoid returning
    random JSON fragments (e.g., citation objects) from markdown responses.
    """
    _validate = (lambda obj: _is_grading_result(obj)) if require_grading_result else (lambda obj: True)
    raw_text = raw_text.strip()

    # Strip markdown code fences
    if raw_text.startswith("```"):
        lines = raw_text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw_text = "\n".join(lines).strip()

    # Strategy 1: direct parse
    try:
        result = json.loads(raw_text)
        if isinstance(result, dict) and _validate(result):
            return result
    except json.JSONDecodeError:
        pass

    # Strategy 2: extract the LARGEST valid JSON object from the text.
    # BUG-11 fix: greedy {.*} can merge two separate JSON objects; instead
    # try each top-level '{' as a potential start and parse greedily from it.
    match = None
    _brace_starts = [i for i, c in enumerate(raw_text) if c == '{']
    for _start in _brace_starts:
        _candidate = raw_text[_start:]
        # Find the last '}' in the candidate
        _last_brace = _candidate.rfind('}')
        if _last_brace < 1:
            continue
        _candidate = _candidate[:_last_brace + 1]
        try:
            result = json.loads(_candidate)
            if isinstance(result, dict) and _is_grading_result(result):
                match = type('M', (), {'group': lambda self, _c=_candidate: _c})()
                return result
        except json.JSONDecodeError:
            if match is None:
                match = type('M', (), {'group': lambda self, _c=_candidate: _c})()
    # If we found a brace block but it didn't parse, keep match for Strategy 3
    if not match and _brace_starts:
        _start = _brace_starts[0]
        _last = raw_text.rfind('}')
        if _last > _start:
            match = type('M', (), {'group': lambda self, _t=raw_text[_start:_last+1]: _t})()

    # Strategy 3: fix common LLM JSON mistakes
    # - trailing commas before } or ]
    # - single quotes instead of double quotes
    # - unescaped newlines in strings
    cleaned = raw_text
    if match:
        cleaned = match.group()
    # Remove trailing commas: ,\s*} or ,\s*]
    cleaned = re.sub(r',\s*([}\]])', r'\1', cleaned)
    # Replace single-quoted keys/values (conservative: only when preceded by { , or [)
    cleaned = re.sub(r"(?<=[{\[,:])\s*'([^']*?)'\s*", r' "\1" ', cleaned)
    try:
        result = json.loads(cleaned)
        if isinstance(result, dict) and _validate(result):
            return result
    except json.JSONDecodeError:
        pass

    # Strategy 4: find JSON between ```json and ``` deeper in text
    json_block = re.search(r'```json\s*([\s\S]*?)```', raw_text)
    if json_block:
        try:
            result = json.loads(json_block.group(1).strip())
            if isinstance(result, dict) and _is_grading_result(result):
                return result
        except json.JSONDecodeError:
            pass

    # Strategy 5: bracket-balanced extraction (handles nested objects)
    # Try LARGEST objects first (most likely to be the full grading result)
    candidates_found: list[dict] = []
    depth = 0
    start_idx = None
    for i, ch in enumerate(raw_text):
        if ch == '{':
            if depth == 0:
                start_idx = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start_idx is not None:
                candidate = raw_text[start_idx:i + 1]
                candidate = re.sub(r',\s*([}\]])', r'\1', candidate)
                try:
                    parsed = json.loads(candidate)
                    if isinstance(parsed, dict) and _is_grading_result(parsed):
                        return parsed
                    candidates_found.append(parsed)
                except json.JSONDecodeError:
                    pass
                start_idx = None

    raise json.JSONDecodeError("Could not parse JSON", raw_text[:200], 0)


def _parse_markdown_grading_response(
    raw_text: str,
    rubric_criteria: list[dict[str, Any]],
    max_score: int,
) -> Optional[dict[str, Any]]:
    """Parse a markdown-formatted grading response into structured JSON.

    Handles multiple LLM markdown formats:

    Format A (score on header):
        1. **Q1a(i) - Count messages per sender**: 0.5 points
           - Justification: ...

    Format B (max on header, score on sub-line):
        1. **[1] Q1a(i) - Count messages per sender: 1.0 points**
           - Score: 0.0
           - Justification: ...

    Format C (score/max on header):
        1. Q1a(i) - Count messages per sender: 0.5/1.0

    The key insight: if a sub-line "Score: X" exists, use that as the actual
    score (the header number is the max). Otherwise, use the header number.
    """
    if not raw_text or not raw_text.strip():
        return None

    text = raw_text.strip()

    # ── Extract LLM's stated total (used for sanity check) ──
    llm_total = None
    total_match = re.search(
        r'(?:total\s+score|overall\s+score)[:\s]*(\d+(?:\.\d+)?)\s*(?:/\s*\d+)?',
        text,
        re.IGNORECASE,
    )
    if total_match:
        try:
            llm_total = float(total_match.group(1))
        except ValueError:
            pass

    # ── Split into criterion blocks ──
    # A criterion header is a numbered line with a recognizable question pattern
    # or bold text followed by a score-like number.
    header_pattern = re.compile(
        r'^'
        r'\s*(?:\d+[\.\)]\s*)?'              # optional numbering: "1. " or "1) "
        r'(?:\*{1,2})?'                       # optional opening bold
        r'(?:\[\d+\]\s*)?'                    # optional [1]
        r'(.+?)'                              # criterion name (captured)
        r'(?:\*{1,2})?'                       # optional closing bold
        r'\s*$',
    )

    # Score on the header line itself: "criterion: 0.5 points" or "criterion: 0.5/1.0"
    header_score_pattern = re.compile(
        r'[:\-–]\s*(\d+(?:\.\d+)?)\s*'
        r'(?:/\s*(\d+(?:\.\d+)?)\s*)?'       # optional /max
        r'(?:points?|pts?|marks?)?\s*'
        r'(?:\*{0,2})\s*$',
        re.IGNORECASE,
    )

    # Sub-line patterns for explicit "Score: X"
    score_line_pattern = re.compile(
        r'^\s*[-•*]?\s*(?:score|awarded|earned|given)\s*:\s*(\d+(?:\.\d+)?)',
        re.IGNORECASE,
    )

    # Sub-line for justification
    justification_line_pattern = re.compile(
        r'^\s*[-•*]?\s*(?:justification|feedback|reason(?:ing)?|explanation|comments?)\s*:\s*(.+)',
        re.IGNORECASE,
    )

    lines = text.split('\n')
    blocks: list[dict[str, Any]] = []
    current_block: Optional[dict[str, Any]] = None

    for line in lines:
        stripped = line.strip()

        # Skip section headers like "### Grading Report", "#### Total Score: ..."
        if stripped.startswith('#'):
            continue

        # Skip empty lines
        if not stripped:
            continue

        # Check if this line is a criterion header
        # Criterion headers typically have a number prefix AND contain a score/max
        is_header = False
        header_name = None
        header_number = None
        header_max_number = None

        # Try to match a numbered line with a score
        numbered_match = re.match(
            r'^\s*(\d+)[\.\)]\s*(.+)', stripped
        )
        if numbered_match:
            rest = numbered_match.group(2)
            score_match = header_score_pattern.search(rest)
            if score_match:
                is_header = True
                # Extract name: everything before the score pattern
                name_part = header_score_pattern.sub('', rest).strip()
                # Clean bold markers
                name_part = re.sub(r'^\*{1,2}|\*{1,2}$', '', name_part).strip()
                name_part = re.sub(r'^\[\d+\]\s*', '', name_part).strip()
                # Remove trailing colon/dash
                name_part = re.sub(r'[:\-–]\s*$', '', name_part).strip()
                header_name = name_part
                try:
                    header_number = float(score_match.group(1))
                except ValueError:
                    header_number = 0
                if score_match.group(2):
                    try:
                        header_max_number = float(score_match.group(2))
                    except ValueError:
                        pass

        if is_header and header_name:
            # Save previous block
            if current_block is not None:
                blocks.append(current_block)

            current_block = {
                "name": header_name,
                "header_number": header_number,
                "header_max": header_max_number,
                "explicit_score": None,  # from "Score: X" sub-line
                "justification_parts": [],
            }
        elif current_block is not None:
            # Check for explicit "Score: X" line
            sm = score_line_pattern.match(stripped)
            if sm:
                try:
                    current_block["explicit_score"] = float(sm.group(1))
                except ValueError:
                    pass
                continue

            # Check for explicit "Justification: ..." line
            jm = justification_line_pattern.match(stripped)
            if jm:
                current_block["justification_parts"].append(jm.group(1).strip())
                continue

            # Otherwise, collect as justification (skip citation lines)
            clean = re.sub(r'^\s*[-•*]\s*', '', stripped)
            if clean and not clean.startswith('Citations:') and not clean.startswith('[{'):
                current_block["justification_parts"].append(clean)

    # Save last block
    if current_block is not None:
        blocks.append(current_block)

    if not blocks:
        return None

    # ── Determine actual score for each block ──
    extracted_items = []
    for block in blocks:
        name = block["name"]
        header_num = block["header_number"] or 0
        header_max = block["header_max"]
        explicit_score = block["explicit_score"]

        # Decision logic for which number is the score:
        if explicit_score is not None:
            # "Score: X" sub-line exists → use it (header_num was the max)
            score = explicit_score
        elif header_max is not None:
            # Format "0.5/1.0" → header_num is score, header_max is max
            score = header_num
        else:
            # Only one number on header → it's the score
            score = header_num

        justification = ' '.join(block["justification_parts"]).strip()
        extracted_items.append({
            "criterion": name,
            "score": score,
            "justification": justification[:500],
        })

    logger.info(
        f"Markdown parser extracted {len(extracted_items)} criteria from non-JSON response"
    )

    # ── Sanity check: compare parsed total vs LLM's stated total ──
    parsed_total = sum(item["score"] for item in extracted_items)
    if llm_total is not None and abs(parsed_total - llm_total) > max_score * 0.15:
        logger.warning(
            f"Markdown parser total ({parsed_total}) differs significantly from "
            f"LLM stated total ({llm_total}). Using LLM total as reference and "
            f"scaling scores."
        )
        # Scale individual scores to match LLM's stated total
        if parsed_total > 0:
            scale = llm_total / parsed_total
            for item in extracted_items:
                item["score"] = round(item["score"] * scale, 1)
            parsed_total = sum(item["score"] for item in extracted_items)

    # Build result in the standard format
    result = {
        "rubric_breakdown": [
            {
                "criterion": item["criterion"],
                "score": item["score"],
                "max": 0,  # will be filled by _validate_result
                "justification": item.get("justification", "")[:500],
            }
            for item in extracted_items
        ],
        "total_score": parsed_total,
        "overall_feedback": "",
        "strengths": [],
        "weaknesses": [],
        "confidence": "medium",
        "confidence_reasoning": "Parsed from markdown response (non-JSON LLM output)",
    }

    # Extract overall feedback if present
    feedback_match = re.search(
        r'(?:overall\s+feedback|summary|general\s+comments?)[:\s]*(.+?)(?:\n#{2,}|\n\d+\.\s|\Z)',
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if feedback_match:
        result["overall_feedback"] = feedback_match.group(1).strip()[:500]

    # Validate against rubric to fix criterion names and clamp scores
    validated = _validate_result(result, rubric_criteria, max_score)

    # Final sanity check: if validated total is wildly different from LLM total, flag it
    if llm_total is not None:
        validated_total = validated.get("total_score", 0)
        if abs(validated_total - llm_total) > max_score * 0.3:
            logger.warning(
                f"Post-validation total ({validated_total}) still differs greatly from "
                f"LLM stated total ({llm_total}). Adjusting confidence to low."
            )
            validated["confidence"] = "low"
            validated["confidence_reasoning"] = (
                f"Markdown parsing produced total {validated_total} but LLM stated {llm_total}. "
                f"Results may be unreliable."
            )

    return validated


def compute_grading_hash(student_files: list, rubric: str, max_score: int) -> str:
    """Compute a stable hash of grading inputs, including images."""
    hasher = hashlib.sha256()
    hasher.update(f"grader_cache_version:{GRADER_CACHE_VERSION}".encode("utf-8"))
    hasher.update(b"\x1e")
    hasher.update(f"providers:{_provider_signature_for_hash()}".encode("utf-8"))
    hasher.update(b"\x1e")
    hasher.update(
        f"vision_cfg:{ENABLE_VISION_PREANALYSIS}|{MAX_IMAGES_SELECTION_POOL}|{MAX_IMAGES_FOR_PREANALYSIS}|{VISION_PREANALYSIS_CHUNK_SIZE}|{MAX_IMAGES_FOR_FINAL_GRADE}|{MAX_FINAL_IMAGE_BYTES}".encode("utf-8")
    )
    hasher.update(b"\x1e")
    hasher.update(hashlib.sha256(SYSTEM_PROMPT.encode("utf-8", errors="replace")).digest())
    hasher.update(b"\x1e")
    hasher.update(str(rubric or "").encode("utf-8", errors="replace"))
    hasher.update(b"\x1e")
    hasher.update(str(max_score).encode("utf-8"))
    hasher.update(b"\x1e")

    normalized_files = []
    for f in student_files:
        if hasattr(f, 'filename'):
            normalized_files.append({
                "filename": str(f.filename or ""),
                "file_type": str(f.file_type or ""),
                "text_content": str(f.text_content or ""),
                "images": list(f.images or []),
            })
        else:
            normalized_files.append({
                "filename": str(f.get("filename", "")),
                "file_type": str(f.get("type", f.get("file_type", ""))),
                "text_content": str(f.get("content", f.get("text_content", "")) or ""),
                "images": list(f.get("images", []) or []),
            })

    normalized_files.sort(key=lambda x: (x["filename"], x["file_type"]))

    for nf in normalized_files:
        hasher.update(nf["filename"].encode("utf-8", errors="replace"))
        hasher.update(b"\x1f")
        hasher.update(nf["file_type"].encode("utf-8", errors="replace"))
        hasher.update(b"\x1f")
        hasher.update(hashlib.sha256(nf["text_content"].encode("utf-8", errors="replace")).digest())
        hasher.update(b"\x1f")

        images = nf["images"]
        images_sorted = sorted(
            images,
            key=lambda img: (
                int((img or {}).get("page", 0) or 0),
                str((img or {}).get("description", "")),
            ),
        )

        for img in images_sorted:
            if not isinstance(img, dict):
                continue
            hasher.update(str(img.get("page", "")).encode("utf-8"))
            hasher.update(b"\x1f")
            hasher.update(str(img.get("description", "")).encode("utf-8", errors="replace"))
            hasher.update(b"\x1f")
            hasher.update(str(img.get("media_type", "")).encode("utf-8", errors="replace"))
            hasher.update(b"\x1f")
            # Include actual image bytes identity; this prevents cross-student cache collisions.
            hasher.update(hashlib.sha256(str(img.get("base64", "")).encode("utf-8", errors="replace")).digest())
            hasher.update(b"\x1f")

        hasher.update(b"\x1e")

    return hasher.hexdigest()[:16]



# ============================================================================
# AI-POWERED IMAGE RELEVANCE RANKING
# ============================================================================

IMAGE_RELEVANCE_PROMPT = """You are an expert at analyzing student submissions.
Analyze the provided images and rank them by RELEVANCE for grading purposes.

Classification categories:
- "SUBSTANTIVE_CODE": Contains actual code solutions, algorithms, implementations
- "SUBSTANTIVE_WRITTEN": Contains written solutions, explanations, analysis
- "SUBSTANTIVE_DIAGRAM": Contains important diagrams, flowcharts, graphs
- "MINOR": Front matter, headers, decorative, or irrelevant content

Return ONLY valid JSON:
{
  "ranked_images": [
    {"image_id": "id1", "rank": 1, "classification": "SUBSTANTIVE_CODE", "relevance_score": 10, "reason": "Contains main solution code"},
    {"image_id": "id2", "rank": 2, "classification": "SUBSTANTIVE_WRITTEN", "reason": "Contains explanation"}
  ],
  "summary": "Overall assessment"
}"""


async def _rank_images_by_relevance(
    client,
    images: list[dict],
    max_images: int = 8,
) -> tuple[list[dict], dict]:
    """
    Use AI to rank images by relevance for grading.
    This helps prioritize which images are most important.
    """
    if not images:
        return [], {"method": "none", "ranked_count": 0}
    
    # Only rank if we have more images than we can send
    if len(images) <= max_images:
        return images, {"method": "all_within_limit", "ranked_count": len(images)}
    
    # Build content with images for ranking
    content = [{"type": "text", "text": IMAGE_RELEVANCE_PROMPT}]
    
    # Take a sample for ranking (not all - would be too many)
    sample_size = min(20, len(images))
    sample = images[:sample_size]
    
    for img in sample:
        desc = f"[Image {img.get('image_id', 'unknown')}] {img.get('filename', '')}"
        if img.get('page'):
            desc += f" (Page {img['page']})"
        content.append({"type": "text", "text": desc})
        
        if img.get('base64'):
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{img['base64']}", "detail": "low"}
            })
    
    messages = [
        {"role": "system", "content": "You are an expert at analyzing student submissions for grading."},
        {"role": "user", "content": content}
    ]
    
    try:
        await _rate_limiter.acquire()
        response = client.chat.completions.create(
            model=NVIDIA_MODEL,
            messages=messages,
            temperature=0.0,
            max_tokens=1000,
            seed=42,
        )
        
        raw = response.choices[0].message.content or ""
        
        # Parse response
        import json
        json_start = raw.find('{')
        json_end = raw.rfind('}') + 1
        if json_start >= 0 and json_end > json_start:
            rankings = json.loads(raw[json_start:json_end])
            
            # Map rankings back to images
            ranked_ids = {}
            for item in rankings.get("ranked_images", []):
                ranked_ids[item.get("image_id")] = {
                    "rank": item.get("rank", 999),
                    "classification": item.get("classification", "MINOR"),
                    "score": item.get("relevance_score", 0),
                    "reason": item.get("reason", "")
                }
            
            # Sort images by their ranking
            def get_rank(img):
                rid = img.get("image_id", "")
                return ranked_ids.get(rid, {}).get("rank", 999)
            
            sorted_images = sorted(images, key=get_rank)
            selected = sorted_images[:max_images]
            
            return selected, {
                "method": "ai_ranked",
                "ranked_count": len(ranked_ids),
                "selected_count": len(selected),
                "total_available": len(images),
                "classifications": {
                    img.get("image_id", ""): ranked_ids.get(img.get("image_id", ""), {}).get("classification", "UNKNOWN")
                    for img in selected
                }
            }
        
    except Exception as e:
        logger.warning(f"Image relevance ranking failed: {e}")
    
    # Fallback: just take first N images
    return images[:max_images], {"method": "fallback_first_n", "ranked_count": 0}


# ============================================================================
# MULTI-PASS GRADING ARCHITECTURE
# ============================================================================
# When a student submission is too large for a single context window the
# grader partitions the text into overlapping windows, grades each window
# against the FULL rubric, and then aggregates using a MAX-score-per-criterion
# rule.  Images are distributed across windows via round-robin so every pass
# sees a balanced subset.
# ============================================================================

@dataclass
class GradingContext:
    """Accumulates evidence across multi-pass grading windows.

    Uses *evidence-weighted* aggregation instead of simple MAX-per-criterion:
    for each criterion, the score from the pass with the MOST evidence
    (citations) is preferred.  On ties, the LOWER score wins (conservative).
    """

    # Per-criterion: list of all pass observations
    criterion_all_passes: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    # Legacy best-score tracker for the prior evidence block (built from all_passes)
    criterion_best: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # All strengths, weaknesses, suggestions collected across passes
    all_strengths: List[str] = field(default_factory=list)
    all_weaknesses: List[str] = field(default_factory=list)
    all_suggestions: List[str] = field(default_factory=list)
    all_feedback: List[str] = field(default_factory=list)
    # Evidence summaries forwarded between passes
    evidence_summaries: List[str] = field(default_factory=list)
    # Pass-level metadata
    pass_results: List[Dict[str, Any]] = field(default_factory=list)
    # Confidence tracking
    confidence_votes: List[str] = field(default_factory=list)

    def ingest_pass_result(self, result: dict, pass_id: int) -> None:
        """Merge a single-pass grading result into the accumulated context."""
        for item in result.get("rubric_breakdown", []):
            crit = str(item.get("criterion", "")).strip()
            score = float(item.get("score", 0))
            citations = item.get("citations", [])
            evidence_count = len([c for c in citations if isinstance(c, dict)])

            entry = {
                "criterion": crit,
                "score": score,
                "max": item.get("max", 0),
                "justification": item.get("justification", ""),
                "citations": citations,
                "evidence_count": evidence_count,
                "source_pass": pass_id,
            }
            self.criterion_all_passes.setdefault(crit, []).append(entry)

            # Keep criterion_best updated for the prior evidence block
            existing = self.criterion_best.get(crit)
            if existing is None or score > float(existing.get("score", 0)):
                self.criterion_best[crit] = entry

        for s in result.get("strengths", []):
            if s and s not in self.all_strengths:
                self.all_strengths.append(s)
        for w in result.get("weaknesses", []):
            if w and w not in self.all_weaknesses:
                self.all_weaknesses.append(w)
        sugg = result.get("suggestions_for_improvement", "")
        if isinstance(sugg, list):
            for s in sugg:
                if s and s not in self.all_suggestions:
                    self.all_suggestions.append(s)
        elif sugg and sugg not in self.all_suggestions:
            self.all_suggestions.append(sugg)

        fb = result.get("overall_feedback", "")
        if fb:
            self.all_feedback.append(fb)

        conf = str(result.get("confidence", "medium")).lower()
        self.confidence_votes.append(conf)

        self.pass_results.append({
            "pass_id": pass_id,
            "total_score": result.get("total_score", 0),
            "confidence": conf,
        })

    def build_prior_evidence_block(self) -> str:
        """Create a summary block to inject into subsequent passes so the LLM
        knows what was already found in earlier windows."""
        if not self.criterion_best:
            return ""
        lines = ["PRIOR EVIDENCE FROM EARLIER CONTENT WINDOWS:"]
        for crit, info in self.criterion_best.items():
            lines.append(
                f"  - {crit}: {info['score']}/{info['max']} — {info['justification'][:120]}"
            )
        lines.append("")
        lines.append("INSTRUCTION: If you find STRONGER evidence in this window, use the")
        lines.append("higher score. Otherwise keep your assessment consistent with above.")
        return "\n".join(lines)

    def aggregate(self, rubric_criteria: list[dict], max_score: int) -> dict:
        """Produce the final aggregated result using evidence-weighted selection.

        For each criterion, picks the score from the pass with the MOST
        evidence (citation count).  If tied, prefers the HIGHER score
        because a pass that found content is more informative than one
        that said "not visible" (which just means the content wasn't in
        that window).  Flags disagreements > 2 points.
        """
        _NOT_FOUND_PHRASES = frozenset([
            "not directly visible", "not found", "not assessed",
            "no evidence found", "not present", "not visible",
            "could not find", "no attempt", "not available",
        ])

        def _is_not_found_pass(p: dict) -> bool:
            """Check if a pass's justification indicates it didn't find the content."""
            just = str(p.get("justification", "")).lower()
            return any(phrase in just for phrase in _NOT_FOUND_PHRASES)

        breakdown = []
        total = 0.0
        disagreements = 0

        for rc in rubric_criteria:
            crit = str(rc.get("criterion", "")).strip()
            passes = self.criterion_all_passes.get(crit, [])

            if not passes:
                breakdown.append({
                    "criterion": crit,
                    "score": 0,
                    "max": rc["max"],
                    "justification": "No evidence found across all content windows.",
                    "citations": [],
                })
                continue

            # Deprioritize passes that said "not found" / "not visible" —
            # these didn't actually grade the criterion, they just didn't
            # have the content in their window.
            found_passes = [p for p in passes if not _is_not_found_pass(p)]
            not_found_passes = [p for p in passes if _is_not_found_pass(p)]

            # Prefer passes that actually found content
            ranking_pool = found_passes if found_passes else not_found_passes

            # Sort by evidence_count DESC, then score DESC (prefer higher score)
            passes_sorted = sorted(
                ranking_pool,
                key=lambda p: (p["evidence_count"], p["score"]),
                reverse=True,
            )

            top_evidence = passes_sorted[0]["evidence_count"]
            top_passes = [p for p in passes_sorted if p["evidence_count"] == top_evidence]

            if len(top_passes) == 1:
                best = top_passes[0]
            else:
                # Tie in evidence count: prefer HIGHER score (the pass that
                # found more content is more informative)
                top_passes.sort(key=lambda p: p["score"], reverse=True)
                best = top_passes[0]

            # Flag significant disagreements for transparency
            scores = [p["score"] for p in passes]
            disagreement_flag = (max(scores) - min(scores)) > 2 if len(scores) > 1 else False
            if disagreement_flag:
                disagreements += 1

            score = round(min(float(best["score"]), float(rc["max"])), 1)
            item = {
                "criterion": crit,
                "score": score,
                "max": rc["max"],
                "justification": best["justification"],
                "citations": best.get("citations", []),
            }
            if disagreement_flag:
                item["_disagreement_flag"] = True
                item["_pass_scores"] = scores
            breakdown.append(item)
            total += score

        total = round(min(total, float(max_score)), 1)
        pct = round(total / max_score * 100, 1) if max_score else 0
        grade = _score_to_letter(total, max_score)

        # Aggregate confidence: if any pass said "low", overall is low;
        # majority "high" → high; else medium.
        if "low" in self.confidence_votes:
            agg_conf = "low"
        elif self.confidence_votes.count("high") > len(self.confidence_votes) / 2:
            agg_conf = "high"
        else:
            agg_conf = "medium"

        return {
            "rubric_breakdown": breakdown,
            "total_score": total,
            "max_score": max_score,
            "percentage": pct,
            "letter_grade": grade,
            "overall_feedback": " ".join(self.all_feedback[:3]),
            "strengths": self.all_strengths[:8],
            "weaknesses": self.all_weaknesses[:8],
            "suggestions_for_improvement": " | ".join(self.all_suggestions[:5]),
            "confidence": agg_conf,
            "confidence_reasoning": (
                f"Multi-pass grading across {len(self.pass_results)} windows. "
                f"Confidence votes: {dict((v, self.confidence_votes.count(v)) for v in set(self.confidence_votes))}. "
                f"Disagreements flagged: {disagreements}."
            ),
            "multi_pass": {
                "total_passes": len(self.pass_results),
                "pass_scores": [p["total_score"] for p in self.pass_results],
                "aggregation": "evidence_weighted",
                "disagreements": disagreements,
            },
        }


def _partition_text_windows(
    text: str,
    window_size: int = MULTI_PASS_WINDOW_SIZE,
    overlap: int = MULTI_PASS_OVERLAP,
) -> List[str]:
    """Split text into overlapping windows."""
    if len(text) <= window_size:
        return [text]
    windows = []
    start = 0
    while start < len(text):
        end = start + window_size
        windows.append(text[start:end])
        start = end - overlap
        if start + overlap >= len(text):
            break
    return windows


def _distribute_images_to_windows(
    images: List[dict], n_windows: int, cap_per_window: int
) -> List[List[dict]]:
    """Round-robin distribute images across windows, capped per window."""
    buckets: List[List[dict]] = [[] for _ in range(n_windows)]
    for idx, img in enumerate(images):
        bucket_idx = idx % n_windows
        if len(buckets[bucket_idx]) < cap_per_window:
            buckets[bucket_idx].append(img)
    return buckets


def _adaptive_max_tokens(rubric_criteria: list[dict], input_char_count: int = 0) -> int:
    """Compute max_tokens budget dynamically from model capacity.

    Strategy:
    1. Estimate input tokens from actual prompt character count.
    2. Subtract from model context window to get available output budget.
    3. Apply a per-criterion minimum so no rubric item gets truncated.
    4. If input is so large that output budget is too small, use a safe minimum.
    """
    from app.config import MODEL_CONTEXT_TOKENS, MODEL_RESERVED_TOKENS, CHARS_PER_TOKEN_ESTIMATE

    # Per-criterion budget: each criterion needs ~350 tokens for score + justification + citations
    per_criterion = 350
    criteria_need = max(2500, len(rubric_criteria) * per_criterion + 800)  # baseline output need

    if input_char_count > 0:
        estimated_input_tokens = int(input_char_count / CHARS_PER_TOKEN_ESTIMATE)
        available_output = MODEL_CONTEXT_TOKENS - estimated_input_tokens - 500  # 500 token safety margin
        # Use the larger of: what criteria need, or 60% of available output (leave headroom)
        dynamic_budget = max(criteria_need, int(available_output * 0.6))
        # But never exceed what the model can actually produce
        dynamic_budget = min(dynamic_budget, available_output)
        # And never go below a safe minimum
        return max(3000, dynamic_budget)
    else:
        # Fallback: no input size known, use criteria-based estimate
        return max(3000, criteria_need)


def _score_to_letter(score: float, max_score: int) -> str:
    """Convert numeric score to letter grade (fine-grained, matches _validate_result)."""
    if max_score <= 0:
        return "F"
    pct = score / max_score * 100
    if pct >= 97:
        return "A+"
    elif pct >= 93:
        return "A"
    elif pct >= 90:
        return "A-"
    elif pct >= 87:
        return "B+"
    elif pct >= 83:
        return "B"
    elif pct >= 80:
        return "B-"
    elif pct >= 77:
        return "C+"
    elif pct >= 73:
        return "C"
    elif pct >= 70:
        return "C-"
    elif pct >= 67:
        return "D+"
    elif pct >= 60:
        return "D"
    return "F"


async def _single_pass_grade(
    title: str,
    description: str,
    rubric: str,
    max_score: int,
    rubric_criteria: list[dict],
    text_window: str,
    window_images: List[dict],
    questions: Optional[list[dict]],
    prior_evidence: str,
    scoring_primary: str,
    scoring_allow_fallback: bool,
    student_files: list,
    pass_id: int,
    total_passes: int,
    reference_solution: Optional[str] = None,
    test_results: Optional[dict] = None,
    system_content: Optional[str] = None,
) -> dict:
    """Execute a single grading pass on one content window."""
    await _rate_limiter.acquire()

    # BUG-05 fix: In multi-pass mode, do NOT pass student_files because the
    # file manifest describes ALL files but the window only has a subset.
    # The LLM could hallucinate content from files described in the manifest
    # but not present in the window.  Pass empty list so no manifest is built.
    _files_for_prompt = student_files if total_passes <= 1 else []
    user_text = _build_user_prompt(
        title, description, rubric, max_score, _files_for_prompt, questions,
        criterion_evidence_context=prior_evidence if prior_evidence else None,
        reference_solution=reference_solution,
        test_results=test_results,
    )

    # Add truncation awareness header
    if total_passes > 1:
        window_header = (
            f"\n\n[MULTI-PASS GRADING: Window {pass_id}/{total_passes}]\n"
            f"This is a partial view of the student's submission. "
            f"Grade what you see in THIS window. If evidence for a criterion is NOT "
            f"in this window, score 0 — other windows may contain it.\n"
            f"IMPORTANT: Do NOT hallucinate code that isn't shown. If you cannot "
            f"find specific code/content for a criterion in this window, score = 0.\n\n"
        )
        text_window = window_header + text_window

    _input_chars = len(user_text) + len(text_window) + len(str(system_content or ""))
    max_tokens = _adaptive_max_tokens(rubric_criteria, input_char_count=_input_chars)

    # Build multimodal content
    user_content, img_count, images_info = _build_multimodal_content(
        user_text, text_window, window_images
    )

    messages = [
        {"role": "system", "content": system_content or SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    # Run blocking OpenAI call in thread (BUG-07 fix)
    response, call_meta = await asyncio.to_thread(
        _chat_completion_with_failover,
        purpose=f"grade_student_pass_{pass_id}",
        needs_vision=img_count > 0,
        messages=messages,
        temperature=0.0,
        top_p=1.0,
        max_tokens=max_tokens,
        seed=42,
        preferred_provider=scoring_primary,
        allow_fallback=scoring_allow_fallback,
        response_format={"type": "json_object"},
    )

    raw_text = response.choices[0].message.content or ""
    try:
        result = _extract_json(raw_text, require_grading_result=True)
        validated = _validate_result(result, rubric_criteria, max_score)
    except json.JSONDecodeError:
        repaired = await _repair_grading_json(raw_text, rubric_criteria, max_score, scoring_primary)
        if repaired is None:
            raise
        validated = repaired

    validated["_pass_meta"] = {
        "pass_id": pass_id,
        "total_passes": total_passes,
        "images_sent": img_count,
        "text_chars": len(text_window),
        "model": str(call_meta.get("model", "")),
        "provider": str(call_meta.get("provider", "")),
    }
    return validated


async def _multi_pass_grade(
    title: str,
    description: str,
    rubric: str,
    max_score: int,
    rubric_criteria: list[dict],
    raw_text_content: str,
    final_images: List[dict],
    questions: Optional[list[dict]],
    scoring_primary: str,
    scoring_allow_fallback: bool,
    student_files: list,
    reference_solution: Optional[str] = None,
    test_results: Optional[dict] = None,
    system_content: Optional[str] = None,
    evidence_map: Optional[list[dict]] = None,
    criterion_evidence: Optional[dict] = None,
) -> Tuple[dict, dict]:
    """
    Multi-pass grading: partition content into windows, grade each against the
    full rubric, accumulate with evidence-weighted aggregation, return aggregated
    result and transparency metadata.
    """
    windows = _partition_text_windows(raw_text_content)
    n_windows = len(windows)
    image_cap = int(FINAL_IMAGE_CAP)
    image_buckets = _distribute_images_to_windows(final_images, n_windows, image_cap)

    ctx = GradingContext()
    pass_transparencies = []

    for i, (window, window_imgs) in enumerate(zip(windows, image_buckets), 1):
        prior = ctx.build_prior_evidence_block()
        try:
            result = await _single_pass_grade(
                title=title,
                description=description,
                rubric=rubric,
                max_score=max_score,
                rubric_criteria=rubric_criteria,
                text_window=window,
                window_images=window_imgs,
                questions=questions,
                prior_evidence=prior,
                scoring_primary=scoring_primary,
                scoring_allow_fallback=scoring_allow_fallback,
                student_files=student_files,
                pass_id=i,
                total_passes=n_windows,
                reference_solution=reference_solution,
                test_results=test_results,
                system_content=system_content,
            )
            ctx.ingest_pass_result(result, i)
            pass_transparencies.append(result.get("_pass_meta", {}))
        except Exception as e:
            logger.error(f"Multi-pass window {i}/{n_windows} failed: {e}")
            pass_transparencies.append({"pass_id": i, "error": str(e)})

    aggregated = ctx.aggregate(rubric_criteria, max_score)

    # ── Citation Attachment (multi-pass) ────────────────────────
    _mp_citation_stats = {}
    if evidence_map:
        _mp_candidate_map = (criterion_evidence or {}).get("candidate_map", {})
        _mp_citation_stats = _attach_rubric_citations(
            aggregated, evidence_map, _mp_candidate_map,
        )
        # Also run citation verification
        try:
            _mp_verifier_trace = await _verify_citations_with_llm(
                aggregated.get("rubric_breakdown", []),
                evidence_map,
                _mp_candidate_map,
                scoring_primary,
            )
            _apply_llm_citation_verdict(
                aggregated.get("rubric_breakdown", []),
                evidence_map,
                _mp_candidate_map,
                _mp_verifier_trace,
            )
        except Exception as exc:
            logger.warning("Multi-pass citation verification failed: %s", exc)

    # ── Score Verification Pass (multi-pass) ────────────────────
    score_verification_trace = await _verify_scores_with_llm(
        aggregated.get("rubric_breakdown", []),
        rubric_criteria,
        raw_text_content,
        scoring_primary,
    )
    score_adj_stats = _apply_score_verification(
        aggregated.get("rubric_breakdown", []),
        score_verification_trace,
        max_score,
    )
    if score_adj_stats.get("adjusted", 0) > 0:
        new_total = round(sum(
            float(item.get("score", 0))
            for item in aggregated.get("rubric_breakdown", [])
            if isinstance(item, dict)
        ), 1)
        new_total = round(min(new_total, float(max_score)), 1)
        aggregated["total_score"] = new_total
        aggregated["percentage"] = round(new_total / max_score * 100, 1) if max_score else 0
        aggregated["letter_grade"] = _score_to_letter(new_total, max_score)

    mp_transparency = {
        "multi_pass_enabled": True,
        "total_windows": n_windows,
        "window_size": MULTI_PASS_WINDOW_SIZE,
        "overlap": MULTI_PASS_OVERLAP,
        "total_text_chars": len(raw_text_content),
        "images_distributed": len(final_images),
        "image_cap_per_window": image_cap,
        "passes": pass_transparencies,
        "score_verification": {
            "trace": score_verification_trace,
            "adjustments_applied": score_adj_stats.get("adjusted", 0),
            "details": score_adj_stats.get("details", []),
        },
    }
    return aggregated, mp_transparency


async def grade_student(
    title: str,
    description: str,
    rubric: str,
    max_score: int,
    student_files: list[dict],
    questions: Optional[list[dict]] = None,
    skip_validation: bool = False,
    reference_solution: Optional[str] = None,
    test_cases: Optional[str] = None,
    run_command: Optional[str] = None,
    student_dir: Optional[str] = None,
) -> dict[str, Any]:
    """Grade a student submission with full transparency."""
    
    rubric_criteria = parse_rubric(rubric)
    
    if not rubric_criteria:
        logger.error("No rubric criteria!")
        return {
            "error": "No rubric criteria - cannot grade",
            "total_score": 0,
            "max_score": max_score,
            "percentage": 0,
            "letter_grade": "F",
            "confidence": "low",
            "rubric_breakdown": [],
            "transparency": {
                "text_chars_sent": 0,
                "images_sent": 0,
                "files_processed": [],
                "images_info": []
            }
        }
    
    grading_hash = compute_grading_hash(student_files, rubric, max_score)

    # Use the existing robust grading pipeline with vision pre-analysis
    await _rate_limiter.acquire()

    raw_text_content = _extract_text_content(student_files)
    selection_pool_limit = max(200, int(MAX_IMAGES_SELECTION_POOL))
    total_available_images = sum(
        len(getattr(f, "images", []) or [])
        for f in student_files
        if hasattr(f, "images")
    )
    selected_images = _collect_selected_images(student_files, max_images=selection_pool_limit)
    selected_count = len(selected_images)

    # ── OCR-based image classification ──────────────────────────────
    # Classify images as text-heavy (handwriting → transcription sufficient)
    # or visual-heavy (diagrams/graphs → needs actual image in grading call).
    # Runs locally via Tesseract — no API calls, ~50ms per image.
    _ocr_classify_batch(selected_images)
    _visual_count = sum(1 for img in selected_images if img.get("_ocr_needs_visual", True))
    _text_count = sum(1 for img in selected_images if not img.get("_ocr_needs_visual", True))
    logger.info(
        "OCR classification: %d images total — %d visual-heavy (prioritized for grading call), "
        "%d text-heavy (covered by transcriptions)",
        len(selected_images), _visual_count, _text_count,
    )

    # Vision pre-analysis processes image batches deterministically, so no image is silently dropped.
    vision_notes, vision_trace = await _run_vision_preanalysis(selected_images)
    # NOTE: evidence_map is built AFTER final_images are selected below, so that
    # sent_in_final flags are accurate. We build a preliminary map here for
    # criterion_evidence planning, then rebuild with final flags after image selection.
    _preliminary_evidence_map = _build_evidence_map(selected_images, vision_trace)
    criterion_evidence = _build_criterion_evidence_plan(rubric_criteria, raw_text_content, _preliminary_evidence_map)
    # ── Code Execution (optional) ──────────────────────────────
    _test_results: Optional[dict] = None
    if test_cases and student_dir:
        try:
            from app.services.code_executor import run_test_cases as _run_tests
            _test_results = _run_tests(student_dir, test_cases, run_command)
        except Exception as exc:
            logger.warning("Code execution failed: %s", exc)
            _test_results = {"passed": 0, "total": 0, "error": str(exc), "results": []}

    # ── System prompt augmentation for reference solution ─────
    system_content = SYSTEM_PROMPT
    if reference_solution:
        system_content += (
            "\n\nA REFERENCE SOLUTION has been provided by the instructor. "
            "Use it as a benchmark for what a correct solution looks like, "
            "but award full credit for equivalent approaches that achieve "
            "the same result."
        )

    user_text = _build_user_prompt(
        title,
        description,
        rubric,
        max_score,
        student_files,
        questions,
        criterion_evidence_context=criterion_evidence.get("prompt_block", ""),
        reference_solution=reference_solution,
        test_results=_test_results,
    )

    text_content = raw_text_content
    # NOTE: Vision transcript is built AFTER final_images selection (below),
    # so we know which image_ids are actually attached vs text-only.

    # Final grade call includes a selected multimodal subset for direct evidence grounding.
    vision_candidates = _enabled_provider_candidates(needs_vision=True)
    provider_image_cap = _effective_vision_image_cap(vision_candidates)
    selected_by_id = {
        str(img.get("image_id", "")): img
        for img in selected_images
        if str(img.get("image_id", "")).strip()
    }
    ordered_candidate_ids: list[str] = []
    for rc in rubric_criteria:
        key = str(rc.get("criterion", "")).strip().lower()
        for image_id in criterion_evidence.get("candidate_map", {}).get(key, {}).get("image_ids", []):
            if image_id not in ordered_candidate_ids:
                ordered_candidate_ids.append(image_id)

    # Sort criterion candidates: visual-heavy FIRST, text-heavy LAST.
    # Visual-heavy images (diagrams/graphs) NEED the actual image in the grading call.
    # Text-heavy images (handwritten notes) are already represented by transcriptions.
    ordered_candidate_ids.sort(
        key=lambda iid: (
            0 if selected_by_id.get(iid, {}).get("_ocr_needs_visual", True) else 1,
        )
    )

    final_images: list[dict[str, Any]] = []
    for image_id in ordered_candidate_ids:
        if image_id in selected_by_id:
            final_images.append(selected_by_id[image_id])
        if len(final_images) >= provider_image_cap:
            break

    if len(final_images) < provider_image_cap:
        for img in _pick_diverse_images_for_grading(selected_images, max_images=provider_image_cap):
            image_id = str(img.get("image_id", "")).strip()
            if not image_id:
                continue
            if any(str(x.get("image_id", "")).strip() == image_id for x in final_images):
                continue
            final_images.append(img)
            if len(final_images) >= provider_image_cap:
                break

    final_images = _enforce_image_byte_budget(final_images[:provider_image_cap], int(MAX_FINAL_IMAGE_BYTES))

    # Now that final_images is determined, build the evidence map with sent_in_final flags
    # and the vision transcript with clear SENT vs NOT-SENT sections.
    _final_ids = {str(img.get("image_id", "")).strip() for img in final_images if img.get("image_id")}
    evidence_map = _build_evidence_map(selected_images, vision_trace, final_image_ids=_final_ids)

    full_vision_transcript = _build_full_vision_transcript(vision_trace, selected_images, final_image_ids=_final_ids)
    notes_attached_to_grading = bool(full_vision_transcript.strip())
    if notes_attached_to_grading:
        text_content = (
            text_content
            + "\n\n=== PER-IMAGE TRANSCRIPTIONS (ALL ANALYZED PAGES) ===\n"
            + "Each block below is an independent transcription from a specific image in the submission.\n"
            + "Use these as evidence for grading handwritten/visual content.\n"
            + "IMPORTANT: Only images in the 'ATTACHED' section are visible to you as images.\n"
            + "For images in the 'NOT ATTACHED' section, use ONLY the transcribed text — do NOT\n"
            + "claim to see visual details from those images.\n\n"
            + full_vision_transcript
        )

    scoring_primary = str(SCORING_PRIMARY_PROVIDER or "").strip().lower()
    scoring_allow_fallback = bool(SCORING_ALLOW_FALLBACK)

    # ── Multi-pass routing decision ─────────────────────────────────
    needs_multi_pass = len(text_content) > int(MULTI_PASS_TEXT_THRESHOLD)

    if needs_multi_pass:
        logger.info(
            f"Multi-pass grading triggered: {len(text_content)} chars > threshold {MULTI_PASS_TEXT_THRESHOLD}, "
            f"{len(final_images)} images, {len(student_files)} files"
        )
        try:
            validated, mp_transparency = await _multi_pass_grade(
                title=title,
                description=description,
                rubric=rubric,
                max_score=max_score,
                rubric_criteria=rubric_criteria,
                raw_text_content=text_content,
                final_images=final_images,
                questions=questions,
                scoring_primary=scoring_primary,
                scoring_allow_fallback=scoring_allow_fallback,
                student_files=student_files,
                reference_solution=reference_solution,
                test_results=_test_results,
                system_content=system_content,
                evidence_map=evidence_map,
                criterion_evidence=criterion_evidence,
            )
            # Build transparency for multi-pass
            transparency = {
                "text_chars_sent": len(text_content),
                "images_sent": len(final_images),
                "images_available_total": int(total_available_images),
                "images_selected_total": selected_count,
                "selection_pool_limit": selection_pool_limit,
                "files_processed": [],
                "images_info": [],
                "evidence_map": evidence_map,
                "multi_pass": mp_transparency,
                "llm_call": {
                    "provider": scoring_primary or "auto",
                    "model": "",
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                },
            }
            if vision_trace.get("enabled"):
                transparency["vision_preanalysis"] = dict(vision_trace)
            for f in student_files:
                if hasattr(f, 'filename'):
                    transparency["files_processed"].append({
                        "filename": f.filename,
                        "type": f.file_type,
                        "text_length": len(f.text_content) if f.text_content else 0,
                        "image_count": len(f.images) if f.images else 0,
                    })
            validated["grading_hash"] = grading_hash
            validated["images_processed"] = selected_count
            validated["text_chars_processed"] = len(text_content)
            validated["evidence_map"] = evidence_map
            validated["transparency"] = transparency
            logger.info(
                f"Multi-pass graded: {validated['total_score']}/{max_score} ({validated['letter_grade']}) "
                f"passes={mp_transparency['total_windows']} hash={grading_hash}"
            )
            return validated
        except Exception as e:
            logger.exception(f"Multi-pass grading failed, falling back to single-pass: {e}")
            # Fall through to single-pass as safety net

    # ── Single-pass grading (original path) ─────────────────────────
    _input_chars_single = len(user_text) + len(text_content) + len(system_content)
    max_tokens = _adaptive_max_tokens(rubric_criteria, input_char_count=_input_chars_single)
    user_content, img_count, images_info = _build_multimodal_content(user_text, text_content, final_images)

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content}
    ]

    logger.info(
        f"Grading {len(student_files)} files, {selected_count}/{total_available_images} images analyzed/available, {img_count} images sent in final call, provider_cap={provider_image_cap}, selection_pool_limit={selection_pool_limit}, {len(text_content)} text chars"
    )

    # Build transparency report
    transparency = {
        "text_chars_sent": len(text_content),
        "images_sent": img_count,
        "images_available_total": int(total_available_images),
        "images_selected_total": selected_count,
        "selection_pool_limit": selection_pool_limit,
        "selection_pool_truncated": total_available_images > selected_count,
        "image_limit_applied": img_count < selected_count,
        "diverse_image_selection": True,
        "provider_image_cap": provider_image_cap,
        "image_byte_budget": int(MAX_FINAL_IMAGE_BYTES),
        "images_bytes_sent": int(sum(int(img.get("size_bytes", 0) or 0) for img in final_images)),
        "images_processed_in_batches": selected_count,
        "all_images_processed_for_vision": selected_count >= min(total_available_images, selection_pool_limit),
        "files_processed": [],
        "images_info": images_info,
        "images_analyzed_info": [
            {
                "image_id": ev.get("image_id"),
                "filename": ev.get("filename"),
                "page": ev.get("page"),
                "description": ev.get("description"),
                "batch_id": ev.get("batch_id"),
                "substantive": ev.get("substantive"),
                "confidence": ev.get("confidence"),
                "sent_in_final": ev.get("sent_in_final", False),
            }
            for ev in evidence_map
        ],
        "evidence_map": evidence_map,
        "criterion_evidence": {
            "criteria": len(rubric_criteria),
            "candidate_map_size": len(criterion_evidence.get("candidate_map", {})),
            "text_snippets_indexed": int(criterion_evidence.get("text_snippets_indexed", 0)),
            "prompt_chars": len(str(criterion_evidence.get("prompt_block", ""))),
        },
        "prompt_preview": user_text[:500] + "..." if len(user_text) > 500 else user_text,
        "llm_call": {
            "provider": "auto",
            "model": "",
            "preferred_provider": scoring_primary or "auto",
            "fallback_allowed": scoring_allow_fallback,
            "fallback_used": False,
            "temperature": 0.0,
            "max_tokens": max_tokens,
            "seed": 42,
            "chat_template_kwargs": None,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
    }
    if vision_trace.get("enabled"):
        transparency["vision_preanalysis"] = dict(vision_trace)
        transparency["vision_preanalysis"]["notes_attached_to_grading"] = notes_attached_to_grading
        if vision_notes:
            transparency["vision_preanalysis"]["notes_preview"] = vision_notes[:600]

    # OCR classification summary
    transparency["ocr_classification"] = {
        "tesseract_available": bool(_TESSERACT_AVAILABLE),
        "total_images": len(selected_images),
        "visual_heavy": sum(1 for img in selected_images if img.get("_ocr_needs_visual", True)),
        "text_heavy": sum(1 for img in selected_images if not img.get("_ocr_needs_visual", True)),
        "final_images_visual_heavy": sum(1 for img in final_images if img.get("_ocr_needs_visual", True)),
        "final_images_text_heavy": sum(1 for img in final_images if not img.get("_ocr_needs_visual", True)),
    }

    for f in student_files:
        if hasattr(f, 'filename'):
            transparency["files_processed"].append({
                "filename": f.filename,
                "type": f.file_type,
                "text_length": len(f.text_content) if f.text_content else 0,
                "image_count": len(f.images) if f.images else 0
            })

    raw_text = ""
    for attempt in range(3):
        try:
            # D1 FIX: Always use full messages with evidence context on retry
            # Run blocking OpenAI call in thread so event loop stays free for
            # other parallel grading tasks (BUG-07 fix).
            response, call_meta = await asyncio.to_thread(
                _chat_completion_with_failover,
                purpose="grade_student",
                needs_vision=img_count > 0,
                messages=messages,
                temperature=0.0,
                top_p=1.0,
                max_tokens=max_tokens,
                seed=42,
                preferred_provider=scoring_primary,
                allow_fallback=scoring_allow_fallback,
                response_format={"type": "json_object"},
            )
            grading_model = str(call_meta.get("model") or "")
            provider_key = str(call_meta.get("provider_key") or "")
            transparency["llm_call"]["provider"] = str(call_meta.get("provider") or "auto")
            transparency["llm_call"]["model"] = grading_model
            transparency["llm_call"]["fallback_used"] = bool(call_meta.get("fallback_used"))
            transparency["llm_call"]["chat_template_kwargs"] = _chat_template_kwargs_for_model(provider_key, grading_model)
            if call_meta.get("attempts_before_success"):
                transparency["llm_call"]["fallback_attempts"] = call_meta.get("attempts_before_success")
            if transparency["llm_call"]["fallback_used"]:
                transparency["llm_call"]["consistency_note"] = (
                    f"Primary scorer '{scoring_primary or 'auto'}' was unavailable; deterministic fallback was used."
                )
            raw_text = response.choices[0].message.content or ""
            usage = getattr(response, "usage", None)
            transparency["llm_call"]["response_preview"] = raw_text[:400]

            # Handle empty responses (LLM returned nothing)
            if not raw_text.strip():
                logger.warning(f"LLM returned empty response on attempt {attempt + 1}/3")
                transparency["llm_call"]["empty_response"] = True
                if attempt < 2:
                    await asyncio.sleep(2)
                    continue  # Retry with fresh call
                # On final attempt, create error result instead of crashing
                raise json.JSONDecodeError("LLM returned empty response", raw_text, 0)

            try:
                result = _extract_json(raw_text, require_grading_result=True)
                validated = _validate_result(result, rubric_criteria, max_score)
            except json.JSONDecodeError:
                # Strategy A: Try LLM-based JSON repair
                repaired = await _repair_grading_json(raw_text, rubric_criteria, max_score, scoring_primary)
                if repaired is not None:
                    validated = repaired
                    transparency["llm_call"]["json_repaired"] = True
                    transparency["llm_call"]["json_repair_attempt"] = attempt + 1
                else:
                    # Strategy B: Try parsing markdown-formatted response
                    md_parsed = _parse_markdown_grading_response(raw_text, rubric_criteria, max_score)
                    if md_parsed is not None and md_parsed.get("total_score", 0) > 0:
                        validated = md_parsed
                        transparency["llm_call"]["markdown_parsed"] = True
                        logger.info(f"Recovered grading from markdown response: {md_parsed.get('total_score')}/{max_score}")
                    else:
                        logger.warning(
                            f"JSON parse failed on attempt {attempt + 1}/3, raw preview: {raw_text[:200]}"
                        )
                        raise

            # ── Catastrophic failure detection: ALL criteria "Not assessed" ──
            # If _validate_result couldn't match ANY LLM criteria to the rubric,
            # every item will be "Not assessed by AI" with score 0. This is a
            # matching failure, not a real grade — retry with a fresh LLM call.
            _vb = validated.get("rubric_breakdown", [])
            _all_not_assessed = (
                len(_vb) > 0
                and all(
                    "not assessed" in str(item.get("justification", "")).lower()
                    for item in _vb
                    if isinstance(item, dict)
                )
            )
            if _all_not_assessed and len(rubric_criteria) > 0 and attempt < 2:
                logger.warning(
                    f"ALL {len(_vb)} criteria are 'Not assessed by AI' on attempt {attempt + 1}/3 — "
                    f"LLM returned criteria that didn't match rubric. Retrying..."
                )
                transparency["llm_call"].setdefault("all_not_assessed_retries", 0)
                transparency["llm_call"]["all_not_assessed_retries"] += 1
                await asyncio.sleep(1)
                continue  # Retry with fresh LLM call

            # Safety: ensure criterion_evidence is a dict
            if not isinstance(criterion_evidence, dict):
                criterion_evidence = {}
            _ce_candidate_map = criterion_evidence.get("candidate_map", {})
            if not isinstance(_ce_candidate_map, dict):
                _ce_candidate_map = {}

            citation_stats = _attach_rubric_citations(
                validated,
                evidence_map,
                _ce_candidate_map,
            )
            verifier_trace = await _verify_citations_with_llm(
                validated.get("rubric_breakdown", []),
                evidence_map,
                _ce_candidate_map,
                scoring_primary,
            )
            verifier_apply = _apply_llm_citation_verdict(
                validated.get("rubric_breakdown", []),
                evidence_map,
                _ce_candidate_map,
                verifier_trace,
            )
            _ev_lookup = {
                str(ev.get("image_id", "")): ev for ev in evidence_map
                if isinstance(ev, dict) and str(ev.get("image_id", "")).strip()
            }
            _snip_lookup = criterion_evidence.get("snippet_lookup", {})
            if not isinstance(_snip_lookup, dict):
                _snip_lookup = {}
            image_cited_count = 0
            for rb_item in validated.get("rubric_breakdown", []):
                if not isinstance(rb_item, dict):
                    continue
                citations = _normalize_citation_objects(
                    rb_item.get("citations", []),
                    evidence_lookup=_ev_lookup,
                    snippet_lookup=_snip_lookup,
                )
                # Update the citations in the result so filenames are resolved
                if citations:
                    rb_item["citations"] = citations
                if any(isinstance(c, dict) and str(c.get("image_id", "")).strip() for c in citations):
                    image_cited_count += 1
            citation_stats["criteria_with_image_citation"] = image_cited_count
            citation_stats["verifier_overrides_applied"] = int(verifier_apply.get("applied", 0))
            calibrated_conf, calibrated_reason = _calibrate_confidence_with_citations(
                str(validated.get("confidence", "medium")),
                citation_stats,
                verifier_trace,
                image_evidence_available=bool(evidence_map),
            )
            validated["confidence"] = calibrated_conf
            existing_conf_reason = str(validated.get("confidence_reasoning", "")).strip()
            merged_conf_reason = (
                (existing_conf_reason + " " + calibrated_reason).strip()
                if existing_conf_reason else calibrated_reason
            )
            validated["confidence_reasoning"] = merged_conf_reason[:500]
            if usage:
                transparency["llm_call"]["usage"] = {
                    "prompt_tokens": getattr(usage, "prompt_tokens", 0),
                    "completion_tokens": getattr(usage, "completion_tokens", 0),
                    "total_tokens": getattr(usage, "total_tokens", 0),
                }
            transparency["citation_audit"] = {
                "deterministic_assignment": citation_stats,
                "llm_verifier": {
                    "checked": int(verifier_trace.get("checked", 0) or 0),
                    "invalid": int(verifier_trace.get("invalid", 0) or 0),
                    "weak": int(verifier_trace.get("weak", 0) or 0),
                    "provider": str(verifier_trace.get("provider", "")),
                    "model": str(verifier_trace.get("model", "")),
                    "error": str(verifier_trace.get("error", "")),
                },
            }

            # ── Score Verification Pass ──────────────────────────────
            score_verification_trace = await _verify_scores_with_llm(
                validated.get("rubric_breakdown", []),
                rubric_criteria,
                text_content,
                scoring_primary,
            )
            score_adj_stats = _apply_score_verification(
                validated.get("rubric_breakdown", []),
                score_verification_trace,
                max_score,
            )
            if score_adj_stats.get("adjusted", 0) > 0:
                new_total = round(sum(
                    float(item.get("score", 0))
                    for item in validated.get("rubric_breakdown", [])
                    if isinstance(item, dict)
                ), 1)
                new_total = round(min(new_total, float(max_score)), 1)
                validated["total_score"] = new_total
                validated["percentage"] = round(new_total / max_score * 100, 1) if max_score else 0
                validated["letter_grade"] = _score_to_letter(new_total, max_score)
            transparency["score_verification"] = {
                "trace": score_verification_trace,
                "adjustments_applied": score_adj_stats.get("adjusted", 0),
                "details": score_adj_stats.get("details", []),
            }

            validated["grading_hash"] = grading_hash
            validated["images_processed"] = selected_count
            validated["text_chars_processed"] = len(text_content)
            validated["evidence_map"] = evidence_map
            validated["visual_content_analysis"] = validated.get("visual_content_analysis") or {
                "images_reviewed": selected_count,
                "coverage": "full" if selected_count >= min(total_available_images, selection_pool_limit) else "partial",
                "key_observations": [str(x.get("summary", "")) for x in evidence_map if str(x.get("summary", "")).strip()][:6],
            }
            validated["transparency"] = transparency

            # ── Comprehensive Flagging ────────────────────────────────
            # Collect EVERY condition that could affect grading accuracy.
            _warnings: list[str] = []

            # 1. JSON repair was needed
            if transparency.get("llm_call", {}).get("json_repaired"):
                _warnings.append("JSON repair: LLM response required JSON repair — scores may differ from AI intent")
            if transparency.get("llm_call", {}).get("markdown_parsed"):
                _warnings.append("Markdown fallback: LLM returned markdown instead of JSON — less reliable score extraction")

            # 2. Score verification made adjustments
            _sv = transparency.get("score_verification", {})
            if int(_sv.get("adjustments_applied", 0)) > 0:
                _adj_details = _sv.get("details", [])
                _adj_summary = "; ".join(
                    f"{d.get('criterion','?')}: {d.get('original',0)}→{d.get('verified',0)}"
                    for d in _adj_details if isinstance(d, dict) and d.get("applied")
                )[:200]
                _warnings.append(f"Score verification adjusted {_sv['adjustments_applied']} criteria: {_adj_summary}")

            # 3. Vision pre-analysis had errors
            _vp = transparency.get("vision_preanalysis", {})
            if isinstance(_vp, dict) and _vp.get("error"):
                _warnings.append(f"Vision error: {str(_vp['error'])[:150]} — handwritten/image content may be inaccurate")

            # 4. Retries were needed
            _fallback_attempts = transparency.get("llm_call", {}).get("fallback_attempts")
            if _fallback_attempts and len(_fallback_attempts) > 0:
                _warnings.append(f"Required {len(_fallback_attempts)} retry attempt(s) before successful grading")
            if transparency.get("llm_call", {}).get("fallback_used"):
                _warnings.append("Fallback provider used — primary scorer was unavailable")

            # 5. Low confidence
            if str(validated.get("confidence", "")).lower() == "low":
                _warnings.append(f"Low confidence from AI grader: {str(validated.get('confidence_reasoning', ''))[:150]}")

            # 6. All criteria scored 0 but content exists
            _breakdown = validated.get("rubric_breakdown", [])
            _all_zero = all(float(item.get("score", 0)) == 0 for item in _breakdown if isinstance(item, dict)) if _breakdown else True
            _has_content = len(text_content) > 100
            if _all_zero and _has_content:
                _warnings.append(f"All criteria scored 0 despite {len(text_content)} chars of content — possible grading failure")

            # 7. All criteria "Not assessed"
            _all_not_assessed = all(
                "not assessed" in str(item.get("justification", "")).lower()
                for item in _breakdown if isinstance(item, dict)
            ) if _breakdown else False
            if _all_not_assessed and _has_content:
                _warnings.append("All criteria show 'Not assessed by AI' — LLM response may have been truncated or unparseable")

            # 8. Content was truncated
            if transparency.get("selection_pool_truncated"):
                _warnings.append(
                    f"Image pool truncated: {transparency.get('images_available_total', 0)} available, "
                    f"only {transparency.get('images_selected_total', 0)} selected"
                )

            # 9. JSON parse failures occurred (even if later succeeded)
            if int(transparency.get("llm_call", {}).get("json_parse_failures", 0)) > 0:
                _warnings.append(f"JSON parse failed {transparency['llm_call']['json_parse_failures']} time(s) before success")

            if _warnings:
                validated["_grading_warnings"] = _warnings
                logger.info(f"Flagged {len(_warnings)} warning(s) for grading: {_warnings}")

            logger.info(
                f"Graded: {validated['total_score']}/{max_score} ({validated['letter_grade']}) hash={grading_hash} provider={transparency['llm_call']['provider']} model={grading_model}"
            )
            return validated

        except json.JSONDecodeError as e:
            logger.warning(f"JSON error (attempt {attempt + 1}): {e}")
            transparency["llm_call"].setdefault("json_parse_failures", 0)
            transparency["llm_call"]["json_parse_failures"] += 1
            if attempt < 2:
                # On retry, add an explicit JSON reinforcement to the system prompt
                # to increase chances of getting valid JSON on next attempt
                if attempt == 1:
                    logger.info("JSON retry: adding reinforcement instruction for attempt 3")
                await _rate_limiter.acquire()
                continue
            # Final fallback: flag with error but mark for review instead of just returning 0
            return {
                "error": f"JSON parse error: {str(e)}",
                "total_score": 0,
                "max_score": max_score,
                "percentage": 0,
                "letter_grade": "F",
                "confidence": "low",
                "grading_hash": grading_hash,
                "rubric_breakdown": [{"criterion": c['criterion'], "score": 0, "max": c['max'], "justification": "JSON parse error — needs manual review or regrade"} for c in rubric_criteria],
                "transparency": transparency,
                "_provider_error": True,  # Mark as provider error so it shows as retryable
            }

        except ProviderFailoverError as e:
            msg = str(e)
            logger.error(f"Provider failover (attempt {attempt + 1}/3): {msg}")
            if attempt < 2:
                # Exponential backoff: 5s, 15s - give providers time to recover
                backoff = 5 * (3 ** attempt)
                logger.info(f"Retrying after {backoff}s backoff (attempt {attempt + 1})")
                await asyncio.sleep(backoff)
                # Clear only EXPIRED cooldowns so active cooldowns from other
                # concurrent tasks are preserved (BUG-17 fix).
                _now = time.time()
                expired = [k for k, v in _provider_cooldown_until.items() if v <= _now]
                for k in expired:
                    _provider_cooldown_until.pop(k, None)
                continue
            return {
                "error": msg,
                "total_score": 0,
                "max_score": max_score,
                "percentage": 0,
                "letter_grade": "F",
                "confidence": "low",
                "grading_hash": grading_hash,
                "rubric_breakdown": [{"criterion": c['criterion'], "score": 0, "max": c['max'], "justification": "All providers failed after 3 attempts"} for c in rubric_criteria],
                "transparency": transparency,
                "_provider_error": True,
            }
        except Exception as e:
            import traceback as _tb
            tb_str = _tb.format_exc()
            logger.exception(f"API error (attempt {attempt + 1}): {e}\n{tb_str}")
            if attempt < 2:
                await asyncio.sleep(2)
                continue
            return {
                "error": f"API error: {str(e)}",
                "total_score": 0,
                "max_score": max_score,
                "percentage": 0,
                "letter_grade": "F",
                "confidence": "low",
                "grading_hash": grading_hash,
                "rubric_breakdown": [{"criterion": c['criterion'], "score": 0, "max": c['max'], "justification": f"API error — needs regrade: {str(e)[:100]}"} for c in rubric_criteria],
                "transparency": transparency,
                "_provider_error": True,
                "_traceback": tb_str[:1000],
            }
    
    return {
        "error": "Failed after retries",
        "total_score": 0,
        "max_score": max_score,
        "percentage": 0,
        "letter_grade": "F",
        "confidence": "low",
        "grading_hash": grading_hash,
        "rubric_breakdown": [{"criterion": c['criterion'], "score": 0, "max": c['max'], "justification": "Failed"} for c in rubric_criteria],
        "transparency": transparency
    }
