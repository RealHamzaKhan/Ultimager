"""
ACMAG: Anchor-Calibrated Multi-Agent Grading.

This module adds a high-transparency grading orchestration layer on top of the base grader:
- calibration subset selection
- anchor bank construction
- dual examiner flow
- moderation on disagreement
- reliability monitoring via quadratic weighted kappa
- per-criterion evidence trail attachment
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

from app.config import (
    ACMAG_ENABLED,
    ACMAG_CALIBRATION_RATIO,
    ACMAG_MIN_CALIBRATION,
    ACMAG_MAX_CALIBRATION,
    ACMAG_BLIND_REVIEW_RATIO,
    ACMAG_KAPPA_THRESHOLD,
    ACMAG_MODERATION_SCORE_DELTA,
    ACMAG_MAX_ANCHORS,
)
from app.services.ai_grader_fixed import (
    grade_student,
    parse_rubric,
    _chat_completion_with_failover,
    _extract_json,
    _validate_result,
)

logger = logging.getLogger(__name__)


def _stable_int(value: str) -> int:
    digest = hashlib.sha256(str(value or "").encode("utf-8", errors="replace")).hexdigest()
    return int(digest[:12], 16)


def _clamp_float(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _score_band(score: float, max_score: int) -> str:
    if max_score <= 0:
        return "U"
    pct = (float(score) / float(max_score)) * 100.0
    if pct >= 90:
        return "A"
    if pct >= 80:
        return "B"
    if pct >= 70:
        return "C"
    if pct >= 60:
        return "D"
    return "F"


def _top_rubric_items(result: dict[str, Any], limit: int = 4) -> list[dict[str, Any]]:
    items = []
    for item in list(result.get("rubric_breakdown", []) or [])[:limit]:
        if not isinstance(item, dict):
            continue
        items.append({
            "criterion": str(item.get("criterion", ""))[:120],
            "score": float(item.get("score", 0) or 0),
            "max": float(item.get("max", 0) or 0),
            "justification": str(item.get("justification", ""))[:240],
        })
    return items


def _criterion_score_signature(result: dict[str, Any]) -> list[tuple[str, float]]:
    sig = []
    for item in list(result.get("rubric_breakdown", []) or []):
        if not isinstance(item, dict):
            continue
        sig.append((str(item.get("criterion", "")).strip().lower(), float(item.get("score", 0) or 0)))
    sig.sort(key=lambda t: t[0])
    return sig


def _max_criterion_delta(a: dict[str, Any], b: dict[str, Any]) -> float:
    map_a = {k: v for k, v in _criterion_score_signature(a)}
    map_b = {k: v for k, v in _criterion_score_signature(b)}
    keys = set(map_a.keys()) | set(map_b.keys())
    if not keys:
        return abs(float(a.get("total_score", 0) or 0) - float(b.get("total_score", 0) or 0))
    return max(abs(float(map_a.get(k, 0.0)) - float(map_b.get(k, 0.0))) for k in keys)


def quadratic_weighted_kappa(scores_a: list[float], scores_b: list[float], max_score: int) -> float:
    """Quadratic weighted Cohen's kappa without external dependencies."""
    if not scores_a or not scores_b or len(scores_a) != len(scores_b):
        return 1.0
    if max_score <= 0:
        return 1.0

    n_bins = int(max_score) + 1
    if n_bins <= 1:
        return 1.0

    matrix = [[0.0 for _ in range(n_bins)] for _ in range(n_bins)]
    for a, b in zip(scores_a, scores_b):
        ia = int(round(_clamp_float(float(a), 0.0, float(max_score))))
        ib = int(round(_clamp_float(float(b), 0.0, float(max_score))))
        matrix[ia][ib] += 1.0

    total = float(len(scores_a))
    if total <= 0:
        return 1.0

    hist_a = [sum(matrix[i][j] for j in range(n_bins)) for i in range(n_bins)]
    hist_b = [sum(matrix[i][j] for i in range(n_bins)) for j in range(n_bins)]

    obs = [[matrix[i][j] / total for j in range(n_bins)] for i in range(n_bins)]
    exp = [[(hist_a[i] * hist_b[j]) / (total * total) for j in range(n_bins)] for i in range(n_bins)]

    denom = float((n_bins - 1) ** 2)
    if denom <= 0:
        return 1.0

    obs_weighted = 0.0
    exp_weighted = 0.0
    for i in range(n_bins):
        for j in range(n_bins):
            w = ((i - j) ** 2) / denom
            obs_weighted += w * obs[i][j]
            exp_weighted += w * exp[i][j]

    if exp_weighted <= 0:
        return 1.0
    return 1.0 - (obs_weighted / exp_weighted)


def _tokenize(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-zA-Z][a-zA-Z0-9_+\-*]{2,}", str(text or "").lower())}


def _snippet_overlap(criterion: str, snippet: str) -> int:
    return len(_tokenize(criterion) & _tokenize(snippet))


def build_evidence_trail(student_files: list[Any], result: dict[str, Any], max_snippets_per_criterion: int = 2) -> list[dict[str, Any]]:
    """
    Build a transparent evidence map for each rubric criterion.
    Uses text snippets + vision notes + image metadata available locally.
    """
    snippets: list[dict[str, str]] = []

    for f in student_files:
        filename = str(getattr(f, "filename", "unknown"))
        text = str(getattr(f, "text_content", "") or "")
        if text:
            lines = [ln.strip() for ln in text.splitlines() if ln and ln.strip()]
            for ln in lines:
                if len(ln) < 16:
                    continue
                snippets.append({
                    "source": f"{filename}:text",
                    "quote": ln[:260],
                })
                if len(snippets) >= 120:
                    break
        for img in list(getattr(f, "images", []) or []):
            desc = str((img or {}).get("description", "")).strip()
            page = (img or {}).get("page", "?")
            if desc:
                snippets.append({
                    "source": f"{filename}:image_page_{page}",
                    "quote": desc[:260],
                })
            if len(snippets) >= 140:
                break

    trace = (((result or {}).get("transparency") or {}).get("vision_preanalysis") or {})
    notes = str(trace.get("notes_preview", "") or "")
    if notes:
        for ln in [x.strip() for x in notes.splitlines() if x.strip()]:
            if len(ln) < 16:
                continue
            snippets.append({
                "source": "vision_preanalysis",
                "quote": ln[:260],
            })
            if len(snippets) >= 180:
                break

    trail: list[dict[str, Any]] = []
    used = set()
    for item in list((result or {}).get("rubric_breakdown", []) or []):
        if not isinstance(item, dict):
            continue
        criterion = str(item.get("criterion", "")).strip()
        if not criterion:
            continue

        ranked = sorted(
            snippets,
            key=lambda s: (_snippet_overlap(criterion, s.get("quote", "")), len(s.get("quote", ""))),
            reverse=True,
        )

        picks = []
        for s in ranked:
            sig = (s.get("source", ""), s.get("quote", ""))
            if sig in used:
                continue
            if _snippet_overlap(criterion, s.get("quote", "")) <= 0 and picks:
                continue
            used.add(sig)
            picks.append({"source": s.get("source", ""), "quote": s.get("quote", "")})
            if len(picks) >= max_snippets_per_criterion:
                break

        trail.append({
            "criterion": criterion,
            "score": item.get("score", 0),
            "max": item.get("max", 0),
            "evidence": picks,
        })
    return trail


@dataclass
class AnchorExample:
    student_id: int
    student_identifier: str
    score: float
    max_score: int
    band: str
    confidence: str
    overall_feedback: str
    rubric_breakdown: list[dict[str, Any]]


@dataclass
class ACMAGRuntime:
    session_id: int
    max_score: int
    submission_ids: list[int]
    submission_identifiers: dict[int, str]
    enabled: bool = ACMAG_ENABLED
    calibration_ratio: float = ACMAG_CALIBRATION_RATIO
    min_calibration: int = ACMAG_MIN_CALIBRATION
    max_calibration: int = ACMAG_MAX_CALIBRATION
    blind_review_ratio: float = ACMAG_BLIND_REVIEW_RATIO
    kappa_threshold: float = ACMAG_KAPPA_THRESHOLD
    moderation_delta: float = ACMAG_MODERATION_SCORE_DELTA
    max_anchors: int = ACMAG_MAX_ANCHORS

    calibration_ids: set[int] = field(default_factory=set)
    anchors: list[AnchorExample] = field(default_factory=list)
    blind_pairs: list[tuple[float, float]] = field(default_factory=list)
    kappa: float = 1.0
    halted: bool = False
    halt_reason: str = ""
    secondary_quota: int = 0
    secondary_used: int = 0
    
    # Thread safety lock for parallel grading
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        self.calibration_ratio = _clamp_float(self.calibration_ratio, 0.0, 1.0)
        self.blind_review_ratio = _clamp_float(self.blind_review_ratio, 0.0, 1.0)
        self.kappa_threshold = _clamp_float(self.kappa_threshold, -1.0, 1.0)
        self.moderation_delta = max(0.0, float(self.moderation_delta))
        self.max_anchors = max(1, int(self.max_anchors))

        total = len(self.submission_ids)
        target = int(math.ceil(total * self.calibration_ratio))
        target = max(int(self.min_calibration), target)
        target = min(int(self.max_calibration), target)
        target = min(target, total)

        ranked = sorted(
            self.submission_ids,
            key=lambda sid: _stable_int(f"{self.session_id}:{self.submission_identifiers.get(sid,'')}:{sid}"),
        )
        self.calibration_ids = set(ranked[:target])

        non_calibration_total = max(0, total - len(self.calibration_ids))
        self.secondary_quota = int(math.ceil(non_calibration_total * self.blind_review_ratio))

    @property
    def calibration_complete(self) -> bool:
        with self._lock:
            calibrated = {a.student_id for a in self.anchors if a.student_id in self.calibration_ids}
            return len(calibrated) >= len(self.calibration_ids)

    def is_calibration_submission(self, submission_id: int) -> bool:
        return submission_id in self.calibration_ids

    def should_run_secondary(self, submission_id: int, student_identifier: str) -> bool:
        if not self.enabled:
            return False
        with self._lock:
            if self.is_calibration_submission(submission_id):
                return True
            if self.secondary_used >= self.secondary_quota:
                return False
            # deterministic sampling for blind second marking
            score = (_stable_int(f"{self.session_id}:{student_identifier}:{submission_id}:blind") % 10000) / 10000.0
            return score < self.blind_review_ratio

    def register_anchor(self, submission_id: int, student_identifier: str, result: dict[str, Any]) -> None:
        if not self.enabled:
            return
        with self._lock:
            if not self.is_calibration_submission(submission_id):
                return
            if any(a.student_id == submission_id for a in self.anchors):
                return
            score = float(result.get("total_score", 0) or 0)
            max_score = int(result.get("max_score", self.max_score) or self.max_score)
            band = _score_band(score, max_score)
            ex = AnchorExample(
                student_id=submission_id,
                student_identifier=student_identifier,
                score=score,
                max_score=max_score,
                band=band,
                confidence=str(result.get("confidence", "medium")),
                overall_feedback=str(result.get("overall_feedback", ""))[:420],
                rubric_breakdown=_top_rubric_items(result, limit=5),
            )
            self.anchors.append(ex)

    def record_secondary_pair(self, primary: dict[str, Any], secondary: dict[str, Any], from_calibration: bool = False) -> None:
        if not self.enabled:
            return
        with self._lock:
            p = float(primary.get("total_score", 0) or 0)
            s = float(secondary.get("total_score", 0) or 0)
            self.blind_pairs.append((p, s))
            if not from_calibration:
                self.secondary_used += 1
            self._refresh_kappa()

    def _refresh_kappa(self) -> None:
        if len(self.blind_pairs) < 2:
            self.kappa = 1.0
            return
        a = [x for x, _ in self.blind_pairs]
        b = [y for _, y in self.blind_pairs]
        self.kappa = float(quadratic_weighted_kappa(a, b, self.max_score))
        if self.kappa < self.kappa_threshold:
            self.halted = True
            self.halt_reason = (
                f"Reliability gate failed: weighted kappa {self.kappa:.3f} < threshold {self.kappa_threshold:.3f}"
            )

    def anchor_context_text(self) -> str:
        with self._lock:
            if not self.anchors:
                return ""

            by_band: dict[str, list[AnchorExample]] = {}
            for a in self.anchors:
                by_band.setdefault(a.band, []).append(a)

            selected: list[AnchorExample] = []
            for band in ["A", "B", "C", "D", "F"]:
                if band in by_band and by_band[band]:
                    selected.append(sorted(by_band[band], key=lambda x: abs(x.score - (0.9 if band == "A" else 0.8) * x.max_score))[0])
            if len(selected) < self.max_anchors:
                remaining = [a for a in self.anchors if a not in selected]
                remaining.sort(key=lambda x: x.score, reverse=True)
                selected.extend(remaining[: max(0, self.max_anchors - len(selected))])
            selected = selected[: self.max_anchors]

            parts = [
                "ACMAG Anchor Context (calibrated examples from this batch):",
                "Use these anchors for consistency, not for copying exact wording.",
            ]
            for idx, a in enumerate(selected, 1):
                parts.append(
                    f"[Anchor {idx}] {a.student_identifier} | band={a.band} | score={a.score:.1f}/{a.max_score} | confidence={a.confidence}"
                )
                if a.rubric_breakdown:
                    mini = "; ".join(
                        f"{it.get('criterion','')}: {it.get('score',0)}/{it.get('max',0)}"
                        for it in a.rubric_breakdown[:4]
                    )
                    parts.append(f"Rubric pattern: {mini}")
                if a.overall_feedback:
                    parts.append(f"Anchor rationale: {a.overall_feedback[:260]}")
            return "\n".join(parts)

    def reliability_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": bool(self.enabled),
                "calibration_target": len(self.calibration_ids),
                "calibration_complete": bool(self.calibration_complete),
                "anchors_built": len(self.anchors),
                "secondary_quota": int(self.secondary_quota),
                "secondary_used": int(self.secondary_used),
                "blind_pairs": len(self.blind_pairs),
                "weighted_kappa": round(float(self.kappa), 4),
                "kappa_threshold": float(self.kappa_threshold),
                "halted": bool(self.halted),
                "halt_reason": str(self.halt_reason),
            }


def _augment_description_for_examiner(
    base_description: str,
    examiner_role: str,
    anchor_context: str,
) -> str:
    role = (examiner_role or "primary").strip().lower()
    role_note = {
        "primary": (
            "ACMAG examiner role: primary examiner.\n"
            "Grade strictly from visible evidence. Use anchors for consistency only."
        ),
        "secondary": (
            "ACMAG examiner role: secondary blind examiner.\n"
            "Independently grade from evidence. Do not mirror likely primary decisions."
        ),
    }.get(role, "ACMAG examiner role: primary examiner.\nGrade strictly from visible evidence.")
    parts = [str(base_description or "").strip(), role_note]
    if anchor_context:
        parts.append(anchor_context)
    parts.append(
        "Transparency requirement: cite concrete evidence in each rubric justification. "
        "If uncertain, choose conservative score and state uncertainty."
    )
    return "\n\n".join(p for p in parts if p).strip()


async def _moderate_disagreement(
    *,
    title: str,
    rubric: str,
    max_score: int,
    primary: dict[str, Any],
    secondary: dict[str, Any],
) -> dict[str, Any]:
    rubric_criteria = parse_rubric(rubric)
    moderator_system = (
        "You are a chief examiner moderating two independent grades. "
        "Choose the more defensible grade based on rubric adherence and evidence quality."
    )
    moderator_user = (
        f"Assignment: {title}\n"
        f"Max score: {max_score}\n"
        f"Rubric:\n{rubric}\n\n"
        f"Primary grade JSON:\n{json.dumps(primary, ensure_ascii=False)[:7000]}\n\n"
        f"Secondary grade JSON:\n{json.dumps(secondary, ensure_ascii=False)[:7000]}\n\n"
        "Return JSON only:\n"
        "{\n"
        '  "decision": "primary|secondary|merge",\n'
        '  "reasoning": "short explicit reason",\n'
        '  "merged_breakdown": [\n'
        '    {"criterion":"<name>", "score": <number>, "max": <number>, "justification":"<text>"}\n'
        "  ]\n"
        "}"
    )

    try:
        response, meta = _chat_completion_with_failover(
            purpose="acmag_moderation",
            needs_vision=False,
            messages=[
                {"role": "system", "content": moderator_system},
                {"role": "user", "content": moderator_user},
            ],
            temperature=0.0,
            top_p=0.1,
            max_tokens=1400,
            seed=42,
        )
        raw = response.choices[0].message.content or ""
        data = _extract_json(raw)
        decision = str(data.get("decision", "primary")).strip().lower()
        reasoning = str(data.get("reasoning", "")).strip()[:800]
        merged = data.get("merged_breakdown", [])

        if decision == "secondary":
            chosen = secondary
        elif decision == "merge" and isinstance(merged, list) and rubric_criteria:
            merged_result = {
                "rubric_breakdown": merged,
                "total_score": sum(float((x or {}).get("score", 0) or 0) for x in merged if isinstance(x, dict)),
                "overall_feedback": str(primary.get("overall_feedback", ""))[:900],
                "strengths": list(primary.get("strengths", []) or [])[:6],
                "weaknesses": list(primary.get("weaknesses", []) or [])[:6],
                "suggestions_for_improvement": str(primary.get("suggestions_for_improvement", ""))[:900],
                "confidence": "medium",
                "confidence_reasoning": f"Chief examiner moderation: {reasoning}",
            }
            chosen = _validate_result(merged_result, rubric_criteria, int(max_score))
        else:
            decision = "primary"
            chosen = primary

        return {
            "decision": decision,
            "reasoning": reasoning or "Chief examiner selected the more rubric-consistent grade.",
            "provider": str(meta.get("provider", "")),
            "model": str(meta.get("model", "")),
            "result": chosen,
        }
    except Exception as exc:
        logger.warning(f"ACMAG moderation failed, using conservative fallback: {exc}")
        # Conservative fallback on disagreement: choose lower total score.
        p = float(primary.get("total_score", 0) or 0)
        s = float(secondary.get("total_score", 0) or 0)
        if s < p:
            return {"decision": "secondary", "reasoning": "Fallback moderation selected lower score.", "provider": "", "model": "", "result": secondary}
        return {"decision": "primary", "reasoning": "Fallback moderation selected lower score.", "provider": "", "model": "", "result": primary}


async def grade_submission_acmag(
    *,
    title: str,
    description: str,
    rubric: str,
    max_score: int,
    student_files: list[Any],
    questions: Optional[list[dict]],
    student_identifier: str,
    anchor_context: str,
    run_secondary: bool,
    moderation_delta: float,
) -> dict[str, Any]:
    """
    Perform ACMAG grading for one submission with optional blind second examiner and moderation.
    """
    primary_desc = _augment_description_for_examiner(description, "primary", anchor_context)
    primary = await grade_student(
        title=title,
        description=primary_desc,
        rubric=rubric,
        max_score=max_score,
        student_files=student_files,
        questions=questions,
    )

    if primary.get("error"):
        primary["acmag"] = {
            "mode": "acmag",
            "examiner_role": "primary",
            "secondary_run": False,
            "moderated": False,
        }
        return {
            "result": primary,
            "primary_result": primary,
            "secondary_result": None,
            "moderation": None,
            "secondary_executed": False,
            "score_delta": None,
            "criterion_delta": None,
        }

    secondary: Optional[dict[str, Any]] = None
    if run_secondary:
        secondary_desc = _augment_description_for_examiner(description, "secondary", anchor_context)
        secondary = await grade_student(
            title=title,
            description=secondary_desc,
            rubric=rubric,
            max_score=max_score,
            student_files=student_files,
            questions=questions,
        )

    final_result = primary
    moderation = None
    score_delta: Optional[float] = None
    criterion_delta: Optional[float] = None

    if secondary and not secondary.get("error"):
        score_delta = abs(float(primary.get("total_score", 0) or 0) - float(secondary.get("total_score", 0) or 0))
        criterion_delta = _max_criterion_delta(primary, secondary)
        if score_delta >= float(moderation_delta) or criterion_delta >= 1.0:
            moderation = await _moderate_disagreement(
                title=title,
                rubric=rubric,
                max_score=max_score,
                primary=primary,
                secondary=secondary,
            )
            final_result = dict(moderation.get("result") or primary)

    evidence_trail = build_evidence_trail(student_files, final_result)
    final_result["evidence_trail"] = evidence_trail
    # Enforce explicit citations for every rubric criterion in ACMAG output.
    trail_map = {str(item.get("criterion", "")).strip(): item for item in evidence_trail if isinstance(item, dict)}
    for rubric_item in list(final_result.get("rubric_breakdown", []) or []):
        if not isinstance(rubric_item, dict):
            continue
        criterion = str(rubric_item.get("criterion", "")).strip()
        evidence = list((trail_map.get(criterion) or {}).get("evidence", []) or [])
        citations = []
        for ev in evidence[:2]:
            if not isinstance(ev, dict):
                continue
            source = str(ev.get("source", "")).strip()
            if not source:
                continue
            citations.append({
                "source": source,
                "quote": str(ev.get("quote", ""))[:180],
            })
        if not citations:
            citations = [{"source": "text_content"}]
        rubric_item["citations"] = citations
        if "Evidence:" not in str(rubric_item.get("justification", "")):
            citation_text = ", ".join(str(c.get("source", "")) for c in citations if c.get("source"))
            if citation_text:
                base = str(rubric_item.get("justification", "") or "").strip()
                rubric_item["justification"] = f"{base} Evidence: {citation_text}".strip()
    final_result["acmag"] = {
        "mode": "acmag",
        "student_identifier": student_identifier,
        "secondary_run": bool(secondary and not secondary.get("error")),
        "score_delta": score_delta,
        "criterion_delta": criterion_delta,
        "moderated": moderation is not None,
        "moderation": {
            "decision": moderation.get("decision", ""),
            "reasoning": moderation.get("reasoning", ""),
            "provider": moderation.get("provider", ""),
            "model": moderation.get("model", ""),
        } if moderation else None,
    }

    return {
        "result": final_result,
        "primary_result": primary,
        "secondary_result": secondary,
        "moderation": moderation,
        "secondary_executed": bool(secondary and not secondary.get("error")),
        "score_delta": score_delta,
        "criterion_delta": criterion_delta,
    }
