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
from dataclasses import dataclass
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
    """Sliding-window rate limiter."""

    def __init__(self, max_requests: int = RATE_LIMIT_RPM, per_seconds: int = 60):
        self.max_requests = max_requests
        self.per_seconds = per_seconds
        self.timestamps: list[float] = []
        self.lock = threading.Lock()

    async def acquire(self):
        with self.lock:
            now = time.time()
            self.timestamps = [t for t in self.timestamps if now - t < self.per_seconds]
            if len(self.timestamps) >= self.max_requests:
                sleep_time = self.per_seconds - (now - self.timestamps[0]) + 0.5
                self.lock.release()
                await asyncio.sleep(sleep_time)
                self.lock.acquire()
                self.timestamps = [t for t in self.timestamps if time.time() - t < self.per_seconds]
            self.timestamps.append(time.time())


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

    for spec, model in candidates:
        provider_key = "nvidia" if spec.name == "nvidia_nim" else spec.name
        client = _get_client(spec)
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
            )
            return response, {
                "provider": provider_key,
                "provider_key": provider_key,
                "model": model,
                "attempts_before_success": attempts,
                "preferred_provider": preferred_key,
                "fallback_used": bool(preferred_key and provider_key != preferred_key),
            }
        except Exception as exc:
            error_type = _classify_provider_error(exc)
            _apply_provider_cooldown(provider_key, error_type)
            status_code = getattr(exc, "status_code", None)
            attempts.append({
                "provider": provider_key,
                "provider_key": provider_key,
                "model": model,
                "error_type": error_type,
                "status_code": status_code,
                "error": str(exc)[:500],
            })
            logger.warning(
                f"{purpose}: provider {spec.name}/{model} failed with {error_type}: {exc}"
            )

    raise ProviderFailoverError(purpose=purpose, attempts=attempts)


def parse_rubric(rubric_text: str) -> list[dict]:
    """Parse rubric text to extract criteria and max points."""
    if not rubric_text or not rubric_text.strip():
        return []
    
    criteria = []
    lines = rubric_text.strip().split('\n')
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        lower_line = line.lower()
        if lower_line.startswith('total') or lower_line.startswith('max'):
            continue
        if lower_line.startswith('rubric'):
            continue
        
        clean_line = re.sub(r'\s*(?:points?|pts?|marks?)\s*$', '', line, flags=re.IGNORECASE)
        numbers = re.findall(r'\b(\d+(?:\.\d+)?)\b', clean_line)
        
        if numbers:
            points = float(numbers[-1])
            if 0 < points < 1000:
                points_str = numbers[-1]
                criterion = re.sub(r'\s*[:\-=]\s*' + re.escape(points_str) + r'.*$', '', clean_line)
                criterion = criterion.strip()
                criterion = re.sub(r'[:\-=]\s*$', '', criterion).strip()
                
                if criterion and len(criterion) > 1:
                    if not any(c['criterion'].lower() == criterion.lower() for c in criteria):
                        criteria.append({"criterion": criterion, "max": points})
    
    return criteria


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
    lines = [f"{c['criterion']}: {int(c['max'])} points" for c in criteria]
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
        out.append({
            "criterion": name,
            "max": _safe_int_points(item.get("max", 1), default=1),
            "description": _normalize_space(item.get("description", ""))[:240],
        })
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


async def generate_rubric_from_description(
    assignment_description: str,
    max_score: int = 100,
    strictness: str = "balanced"
) -> dict:
    """Generate a rubric from assignment description using AI."""
    await _rate_limiter.acquire()
    
    strictness_prompts = {
        "lenient": """
Create a LENIENT rubric that:
- Focuses on effort and completion over perfection
- Gives partial credit generously
- Emphasizes learning and improvement
- Penalizes minor issues lightly
- Rewards creativity and attempts""",
        "balanced": """
Create a BALANCED rubric that:
- Rewards correct implementation appropriately
- Considers both correctness and code quality
- Gives fair partial credit
- Balances strictness with encouragement""",
        "strict": """
Create a STRICT rubric that:
- Requires full correctness for full points
- Penalizes errors and missing requirements
- Emphasizes best practices and edge cases
- Has high standards for code quality
- Little tolerance for incomplete solutions"""
    }
    
    if strictness not in strictness_prompts:
        strictness = "balanced"
    
    strictness_text = strictness_prompts[strictness]
    
    system_prompt = f"""You are an expert Computer Science instructor creating grading rubrics.

{strictness_text}

Create a rubric that sums to exactly {max_score} points.

Hard requirements:
- Criterion names MUST be assignment-specific and skill-based.
- NEVER use placeholder names like "Criterion 1", "Criterion 2", "Problem 1", "Question 3" by themselves.
- Criterion names must include what is being assessed (e.g., implementation, correctness, analysis, comparison, testing).
- Include enough criteria to cover the assignment comprehensively (typically 3 to 7).
- Use integer points only.

Respond in this exact JSON format:
{{
  "rubric_text": "<criterion name>: <points> points\\n...\\nTotal: {max_score}",
  "criteria": [
    {{"criterion": "<specific criterion name>", "max": <integer>, "description": "<specific assessment focus>"}}
  ],
  "strictness_level": "{strictness}",
  "max_score": {max_score},
  "reasoning": "Brief explanation of why these criteria were chosen"
}}"""

    user_prompt = f"""Assignment Description:
{assignment_description}

Generate a complete grading rubric for this assignment."""

    try:
        response, _meta = _chat_completion_with_failover(
            purpose="generate_rubric",
            needs_vision=False,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,
            top_p=0.2,
            max_tokens=1800,
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
        reasoning = _normalize_space(result.get("reasoning", ""))
        if quality_issues:
            note = ", ".join(quality_issues)
            reasoning = _normalize_space(f"{reasoning} Quality fixes applied: {note}.")
        
        return {
            "success": True,
            "rubric_text": rubric_text,
            "criteria": final_criteria,
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
            "criteria": fallback_criteria,
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

    if len(total_text.strip()) < 50 and not has_any_images:
        return {
            "is_relevant": False,
            "confidence": "high",
            "flags": ["empty_submission"],
            "reasoning": "Submission contains minimal or no content",
            "has_relevant_sections": False,
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
        )
        
        raw_text = response.choices[0].message.content or ""
        result = _extract_json(raw_text)

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


def evaluate_relevance_gate(relevance: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Decide whether grading should be blocked due to strong irrelevance signals."""
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

    block_grading = False
    reason = ""
    if "empty_submission" in critical_flags:
        block_grading = True
        reason = "Submission appears empty or unreadable."
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


SYSTEM_PROMPT = """You are an expert Computer Science instructor grading student submissions.

CRITICAL INSTRUCTIONS FOR IMAGE ANALYSIS:
1. Carefully analyze ALL provided images, including handwritten notes, diagrams, and screenshots.
2. If OCR text is incomplete, rely on visual evidence from the images.
3. Grade based on BOTH extracted text and visual evidence.
4. Mention concrete visual evidence when awarding points.
5. Do NOT mark a submission blank if at least one image shows meaningful work.

DETERMINISM PROTOCOL (HIGH PRIORITY):
1. For the same evidence and rubric, return the same scores.
2. Use stable deductions tied to missing requirements, not style preferences.
3. If uncertain between two adjacent scores, choose the lower one unless explicit evidence supports the higher score.
4. Do not inflate scores for assumed work; grade only verifiable content.

GRADING RULES:
1. Grade ONLY what is visible or explicitly present in text.
2. rubric_breakdown criteria names MUST match the provided rubric criteria exactly.
3. "max" MUST match rubric max values exactly.
4. Sum of rubric scores MUST equal total_score.
5. total_score MUST be within [0, max_score].
6. Every rubric item must include short evidence-based justification.

RESPONSE FORMAT - return ONLY valid JSON:
{
  "rubric_breakdown": [
    {
      "criterion": "<EXACT name from rubric>",
      "score": <number>,
      "max": <exact max from rubric>,
      "justification": "<specific evidence>",
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
    
    parts.append("RUBRIC (USE THESE EXACT CRITERIA IN YOUR RESPONSE):")
    for item in rubric_criteria:
        parts.append(f"  - {item['criterion']}: {item['max']} points")
    parts.append(f"  TOTAL: {max_score} points")
    parts.append("")
    
    parts.append("SUBMITTED FILES:")
    for i, f in enumerate(student_files, 1):
        if hasattr(f, 'filename'):
            fn = f.filename
            ft = f.file_type
            img_count = len(f.images) if f.images else 0
            text_len = len(f.text_content) if f.text_content else 0
            if img_count > 0:
                parts.append(f"  {i}. {fn} ({ft} - {img_count} images, {text_len} text chars)")
            else:
                parts.append(f"  {i}. {fn} ({ft} - {text_len} chars)")
        elif isinstance(f, dict):
            fn = f.get('filename', 'unknown')
            ft = f.get('file_type', f.get('type', 'unknown'))
            img_count = len(f.get('images', []))
            text_len = len(f.get('text_content', '')) if f.get('text_content') else 0
            if img_count > 0:
                parts.append(f"  {i}. {fn} ({ft} - {img_count} images, {text_len} text chars)")
            else:
                parts.append(f"  {i}. {fn} ({ft} - {text_len} chars)")
    
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
    parts.append("IMPORTANT: Carefully analyze all images provided. They may contain handwritten solutions, graphs, and diagrams.")
    
    return "\n".join(parts)


def _extract_text_content(student_files: list, max_chars: int = 30000) -> str:
    """Extract text content from code/text files."""
    text_parts = []
    total_chars = 0

    for f in student_files:
        if total_chars >= max_chars:
            text_parts.append("\n[TRUNCATED]")
            break

        if hasattr(f, 'text_content'):
            file_type = f.file_type
            filename = f.filename
            content = f.text_content
        elif hasattr(f, 'content'):
            file_type = getattr(f, 'type', 'code')
            filename = getattr(f, 'filename', 'unknown')
            content = f.content
        else:
            # Handle dict format (from main.py)
            file_type = f.get("file_type", f.get("type", ""))
            filename = f.get("filename", "unknown")
            content = f.get("text_content", f.get("content"))

        if file_type in ("image", "error", "missing", "binary", "archive"):
            continue

        if content is None:
            continue

        if isinstance(content, str) and content.strip():
            header = f"\n=== {filename} ({file_type}) ===\n"
            text_parts.append(header)
            remaining = max_chars - total_chars - len(header)
            text_parts.append(content[:remaining])
            total_chars += len(header) + len(content[:remaining])

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

    seen: set[tuple[str, int, str, int, str]] = set()
    for img in prioritized + remaining:
        if len(selected) >= max_images:
            break
        if not img["base64"]:
            continue
        sig = (
            img["filename"],
            int(img["page"] or 0),
            img["description"],
            int(img["size_bytes"] or 0),
            img["base64"][:48],
        )
        if sig in seen:
            continue
        seen.add(sig)
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
                    "summary": str(item.get("summary", "")).strip()[:320],
                    "transcription": str(item.get("transcription", "")).strip()[:420],
                    "substantive": bool(item.get("substantive", False)),
                    "confidence": str(item.get("confidence", "")).strip().lower()[:20],
                })
    except Exception:
        entries = []

    if entries:
        return entries

    fallback_excerpt = raw_text.strip()[:360]
    for img in chunk:
        entries.append({
            "image_id": str(img.get("image_id", "")),
            "summary": fallback_excerpt,
            "transcription": "",
            "substantive": bool(fallback_excerpt),
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
                top_p=0.1,
                max_tokens=1200,
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
            }
        except Exception as exc:
            error_type = _classify_provider_error(exc)
            _apply_provider_cooldown(provider_key, error_type)
            attempts.append({
                "provider": provider_key,
                "provider_key": provider_key,
                "model": model,
                "error_type": error_type,
                "error": str(exc)[:500],
            })
            logger.warning(
                "vision_preanalysis: provider %s/%s failed with %s: %s",
                spec.name,
                model,
                error_type,
                exc,
            )

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
            top_p=0.1,
            max_tokens=1200,
            seed=42,
        )
        trace["provider"] = meta.get("provider", "")
        trace["model"] = meta.get("model", "")
        merged = (response.choices[0].message.content or "").strip()
        return merged[:12000], trace
    except Exception as exc:
        trace["error"] = str(exc)
        fallback = "\n\n".join(f"[Batch {b.get('batch_id')}]\n{str(b.get('notes',''))[:1600]}" for b in batch_notes)
        return fallback[:12000], trace


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
    consolidated, consolidation_trace = await _consolidate_vision_notes(batch_notes)
    trace["consolidation"] = consolidation_trace

    if consolidated.strip():
        return consolidated[:12000], trace
    merged = "\n\n".join(all_notes).strip()
    return merged[:12000], trace


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
    Goals:
    - Ensure broad file/question coverage first (at least one strong image per file when possible).
    - Prefer focus images over full-page images.
    - Then fill remaining slots by visual evidence quality.
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

    by_file: dict[str, list[dict]] = {}
    for img in images:
        by_file.setdefault(str(img.get("filename", "unknown")), []).append(img)

    # 1) Coverage pass: choose best focus (or best fallback) per file.
    file_best: list[tuple[float, str, dict]] = []
    for fname, file_imgs in by_file.items():
        focus_imgs = [x for x in file_imgs if x.get("is_focus")]
        candidate_pool = focus_imgs if focus_imgs else file_imgs
        best = max(
            candidate_pool,
            key=lambda x: (
                float(x.get("content_score", 0.0) or 0.0),
                int(x.get("size_bytes", 0) or 0),
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

    # 2) Fill remaining slots: prefer focus images, then by score/size.
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

    return chosen


def _tokenize_for_citation(text: str) -> set[str]:
    return {tok for tok in re.findall(r"[a-zA-Z][a-zA-Z0-9_+\-*]{2,}", str(text or "").lower())}


_CITATION_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "are", "was", "were", "have", "has",
    "into", "onto", "using", "use", "used", "task", "question", "problem", "part", "section",
    "criterion", "criteria", "points", "score", "max", "implementation", "analysis", "design",
    "system", "algorithm", "approach", "student", "solution", "code", "report", "work",
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

    return {
        "prompt_block": prompt_block,
        "candidate_map": candidate_map,
        "text_snippets_indexed": len(snippets),
    }


def _build_evidence_map(selected_images: list[dict], vision_trace: dict[str, Any]) -> list[dict[str, Any]]:
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
            target["summary"] = str(entry.get("summary", ""))[:320]
            target["transcription"] = str(entry.get("transcription", ""))[:420]

    return sorted(by_id.values(), key=lambda x: x["image_id"])


def _normalize_citation_objects(raw_citations: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    if not isinstance(raw_citations, list):
        return normalized
    for raw in raw_citations[:8]:
        if isinstance(raw, str):
            image_id = raw.strip()
            if image_id.startswith("img_"):
                normalized.append({"type": "image", "image_id": image_id})
            continue
        if not isinstance(raw, dict):
            continue
        image_id = str(raw.get("image_id", "")).strip()
        snippet_id = str(raw.get("snippet_id", "")).strip()
        source = str(raw.get("source", "")).strip()
        item: dict[str, Any] = {}
        if image_id:
            item["type"] = "image"
            item["image_id"] = image_id
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
        allowed_ids = set(candidate_ids) if candidate_ids else set(evidence_by_id.keys())

        normalized_existing = _normalize_citation_objects(item.get("citations", []))
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

        if not chosen_images:
            scored_candidates: list[tuple[float, int, dict[str, Any]]] = []
            pool = candidate_ids if candidate_ids else list(allowed_ids)
            for image_id in pool:
                ev = evidence_by_id.get(image_id)
                if not ev:
                    continue
                score, overlap = _score_image_evidence_for_criterion(ev, c_tokens)
                scored_candidates.append((score, overlap, ev))
            scored_candidates.sort(key=lambda t: (t[0], t[1]), reverse=True)
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
                    "page": ev.get("page"),
                    "batch_id": ev.get("batch_id"),
                })
            stats["criteria_with_image_citation"] += 1
        else:
            text_ids = criterion_candidates.get("text_snippet_ids", [])
            if text_ids:
                citations.append({"type": "text", "snippet_id": text_ids[0], "source": "text_content"})
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
            for c in _normalize_citation_objects(item.get("citations", []))
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
            top_p=0.1,
            max_tokens=1200,
            seed=42,
            preferred_provider=preferred_provider,
            allow_fallback=bool(SCORING_ALLOW_FALLBACK),
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
            top_p=0.1,
            max_tokens=1800,
            seed=42,
            preferred_provider=preferred_provider,
            allow_fallback=bool(SCORING_ALLOW_FALLBACK),
        )
        repaired_raw = (response.choices[0].message.content or "").strip()
        repaired = _extract_json(repaired_raw)
        return _validate_result(repaired, rubric_criteria, max_score)
    except Exception:
        return None


def _validate_result(result: dict, rubric_criteria: list[dict], max_score: int) -> dict:
    """Validate and fix the grading result."""
    
    rubric_map = {c['criterion'].lower().strip(): c for c in rubric_criteria}
    
    ai_breakdown = result.get("rubric_breakdown", [])
    fixed_breakdown = []
    used_keys = set()
    
    for item in ai_breakdown:
        ai_criterion = str(item.get("criterion", "")).strip()
        ai_key = ai_criterion.lower()
        
        if not ai_criterion:
            continue
        
        matched_key = None
        matched_data = None
        
        if ai_key in rubric_map and ai_key not in used_keys:
            matched_key = ai_key
            matched_data = rubric_map[ai_key]
        
        if not matched_data:
            for rubric_key, rubric_data in rubric_map.items():
                if rubric_key not in used_keys:
                    if rubric_key in ai_key or ai_key in rubric_key:
                        matched_key = rubric_key
                        matched_data = rubric_data
                        break
        
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
    
    for rubric_key, rubric_data in rubric_map.items():
        if rubric_key not in used_keys:
            fixed_breakdown.append({
                "criterion": rubric_data['criterion'],
                "score": 0,
                "max": rubric_data['max'],
                "justification": "Not assessed by AI"
            })
    
    total = 0
    for item in fixed_breakdown:
        try:
            total += float(item.get("score", 0))
        except (ValueError, TypeError):
            pass
    
    total = round(total, 1)
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


def _extract_json(raw_text: str) -> dict:
    """Extract JSON from LLM response."""
    raw_text = raw_text.strip()
    
    if raw_text.startswith("```"):
        lines = raw_text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw_text = "\n".join(lines).strip()
    
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        pass
    
    match = re.search(r'\{[\s\S]*\}', raw_text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    
    raise json.JSONDecodeError("Could not parse JSON", raw_text, 0)


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



async def grade_student(
    title: str,
    description: str,
    rubric: str,
    max_score: int,
    student_files: list[dict],
    questions: Optional[list[dict]] = None,
    skip_validation: bool = False,
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

    # Vision pre-analysis processes image batches deterministically, so no image is silently dropped.
    vision_notes, vision_trace = await _run_vision_preanalysis(selected_images)
    evidence_map = _build_evidence_map(selected_images, vision_trace)
    criterion_evidence = _build_criterion_evidence_plan(rubric_criteria, raw_text_content, evidence_map)
    user_text = _build_user_prompt(
        title,
        description,
        rubric,
        max_score,
        student_files,
        questions,
        criterion_evidence_context=criterion_evidence.get("prompt_block", ""),
    )

    text_content = raw_text_content
    notes_attached_to_grading = bool((vision_notes or "").strip())
    if notes_attached_to_grading:
        text_content = (
            text_content
            + "\n\n=== AUTO VISION NOTES (CHUNKED IMAGE TRANSCRIPTION + OBSERVATIONS) ===\n"
            + "These notes were generated from deterministic image chunking and consolidation.\n"
            + "Use these notes as supplemental evidence across all analyzed pages.\n"
            + vision_notes
        )

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
    user_content, img_count, images_info = _build_multimodal_content(user_text, text_content, final_images)
    scoring_primary = str(SCORING_PRIMARY_PROVIDER or "").strip().lower()
    scoring_allow_fallback = bool(SCORING_ALLOW_FALLBACK)
    fallback_user_text = _build_user_prompt(
        title,
        description,
        rubric,
        max_score,
        student_files,
        questions,
        criterion_evidence_context=None,
    )
    fallback_user_content, _, _ = _build_multimodal_content(fallback_user_text, text_content, final_images)
    
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content}
    ]
    fallback_messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": fallback_user_content}
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
            "max_tokens": 3000,
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
            active_messages = messages if attempt == 0 else fallback_messages
            response, call_meta = _chat_completion_with_failover(
                purpose="grade_student",
                needs_vision=img_count > 0,
                messages=active_messages,
                temperature=0.0,
                top_p=0.1,
                max_tokens=3000,
                seed=42,
                preferred_provider=scoring_primary,
                allow_fallback=scoring_allow_fallback,
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

            try:
                result = _extract_json(raw_text)
                validated = _validate_result(result, rubric_criteria, max_score)
            except json.JSONDecodeError:
                repaired = await _repair_grading_json(raw_text, rubric_criteria, max_score, scoring_primary)
                if repaired is None:
                    raise
                validated = repaired
                transparency["llm_call"]["json_repaired"] = True
                transparency["llm_call"]["json_repair_attempt"] = attempt + 1

            citation_stats = _attach_rubric_citations(
                validated,
                evidence_map,
                criterion_evidence.get("candidate_map", {}),
            )
            verifier_trace = await _verify_citations_with_llm(
                validated.get("rubric_breakdown", []),
                evidence_map,
                criterion_evidence.get("candidate_map", {}),
                scoring_primary,
            )
            verifier_apply = _apply_llm_citation_verdict(
                validated.get("rubric_breakdown", []),
                evidence_map,
                criterion_evidence.get("candidate_map", {}),
                verifier_trace,
            )
            image_cited_count = 0
            for rb_item in validated.get("rubric_breakdown", []):
                citations = _normalize_citation_objects(rb_item.get("citations", []))
                if any(str(c.get("image_id", "")).strip() for c in citations):
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

            logger.info(
                f"Graded: {validated['total_score']}/{max_score} ({validated['letter_grade']}) hash={grading_hash} provider={transparency['llm_call']['provider']} model={grading_model}"
            )
            return validated

        except json.JSONDecodeError as e:
            logger.warning(f"JSON error (attempt {attempt + 1}): {e}")
            if attempt < 2:
                await _rate_limiter.acquire()
                continue
            return {
                "error": f"JSON parse error: {str(e)}",
                "total_score": 0,
                "max_score": max_score,
                "percentage": 0,
                "letter_grade": "F",
                "confidence": "low",
                "grading_hash": grading_hash,
                "rubric_breakdown": [{"criterion": c['criterion'], "score": 0, "max": c['max'], "justification": "Parse error"} for c in rubric_criteria],
                "transparency": transparency
            }

        except ProviderFailoverError as e:
            msg = str(e)
            logger.error(msg)
            return {
                "error": msg,
                "total_score": 0,
                "max_score": max_score,
                "percentage": 0,
                "letter_grade": "F",
                "confidence": "low",
                "grading_hash": grading_hash,
                "rubric_breakdown": [{"criterion": c['criterion'], "score": 0, "max": c['max'], "justification": "All providers failed"} for c in rubric_criteria],
                "transparency": transparency
            }
        except Exception as e:
            logger.exception(f"API error (attempt {attempt + 1})")
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
                "rubric_breakdown": [{"criterion": c['criterion'], "score": 0, "max": c['max'], "justification": "API error"} for c in rubric_criteria],
                "transparency": transparency
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
