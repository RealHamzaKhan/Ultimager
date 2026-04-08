"""Domain Judge Agent — the universal grader.

This is the ONLY agent that calls an LLM. It grades like a real human
professor: partial credit for genuine attempts, fair on phrasing differences,
clear reasoning always recorded.

Single model: NVIDIA LLM (Llama 4 Maverick) for all checkpoint types.
GLM was disabled — it returned empty strings for prompts > ~5K chars.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from openai import OpenAI

from app.config import NVIDIA_API_KEY, NVIDIA_BASE_URL, NVIDIA_MODEL, NVIDIA_MAX_IMAGES_PER_REQUEST
from app.services.agents.base import CheckpointResult

logger = logging.getLogger(__name__)


def _safe_content(response) -> str:
    """Return message content or empty string — never raises on empty choices."""
    try:
        if response and response.choices:
            return (response.choices[0].message.content or "").strip()
    except Exception:
        pass
    return ""


# ── Prompt ────────────────────────────────────────────────────────

JUDGE_SYSTEM_PROMPT = """You are an experienced professor grading a student's submission.

YOUR GRADING PHILOSOPHY:
You grade the way a fair, experienced human professor would — not like an automated system.

CORE PRINCIPLES:
1. Award PARTIAL CREDIT for genuine attempts. A student who clearly tried deserves recognition.
2. Code that does not run but shows correct logic and understanding still deserves credit.
3. Minor syntax errors, typos, or informal phrasing do NOT mean zero marks.
4. Grade the INTENT and UNDERSTANDING, not just perfect execution.
5. If a student writes something informally (e.g. "performance:-reach broom fastest"),
   ask yourself: does this show the right understanding? If yes, give credit.
6. Be constructive. Your reasoning will be shown to the student.
7. Avoid being harsh on format — grade the substance.

SCORING SCALE — pick the most appropriate:
  FULL   (100%) — Correct and complete. No significant issues.
  GOOD   ( 75%) — Mostly correct. Minor gap, small error, or slightly incomplete.
  PARTIAL( 50%) — Shows clear understanding but has notable errors or is incomplete.
  ATTEMPT( 25%) — Genuine attempt. Shows some relevant knowledge. Clearly tried.
  NONE   (  0%) — Not attempted, completely off-topic, or pure guesswork with no merit.

For code specifically:
  - Code that doesn't run due to a missing import but has correct logic → GOOD or FULL
  - Code with a minor syntax error but correct algorithm → GOOD or PARTIAL
  - Code with the right approach but wrong implementation → PARTIAL
  - Code that is completely wrong algorithm → NONE

VISUAL SUBMISSIONS:
When images are attached (diagrams, photos, scanned answers, sketches, engineering drawings):
  - Examine every image carefully before grading.
  - Use [VISUAL] prefix in evidence_quote to describe what you observe in the image.
    Example: "[VISUAL] Student drew a correct BST with root 5, left child 3, right child 7"
  - Grade the visual content with the same partial credit rules as text.
  - A hand-drawn diagram that is mostly correct still earns GOOD or FULL credit.
  - If the submission is image-only (no text), base your entire grade on the images.

IMPORTANT: You must respond with ONLY valid JSON. No extra text before or after.
"""

JUDGE_USER_TEMPLATE = """ASSIGNMENT: {title}

CRITERION: {criterion}
Points available: {points}

WHAT A FULL-CREDIT ANSWER LOOKS LIKE:
{pass_description}

WHAT A ZERO-CREDIT ANSWER LOOKS LIKE:
{fail_description}

STUDENT SUBMISSION:
{content}

Grade this criterion for this student. Respond ONLY in this JSON format:
{{
  "score_percent": <0, 25, 50, 75, or 100>,
  "reasoning": "<Explain as a professor. Reference specific parts. Be fair and constructive. 2-4 sentences.>",
  "evidence_quote": "<Copy the EXACT text or code from the submission that best supports your decision. If nothing relevant exists, write 'No relevant content found.'>",
  "source_file": "<filename where evidence was found, or 'unknown'>",
  "confidence": "<high|medium|low>"
}}"""


# ── LLM client ────────────────────────────────────────────────────

def _get_nvidia_client() -> OpenAI:
    return OpenAI(
        base_url=NVIDIA_BASE_URL,
        api_key=NVIDIA_API_KEY,
        timeout=120.0,
    )


# ── Core judge call ───────────────────────────────────────────────

async def judge_checkpoint(
    checkpoint_id: str,
    criterion: str,
    points_max: float,
    pass_description: str,
    fail_description: str,
    submission_content: str,
    title: str = "",
    rate_limiter=None,
    submission_images: list[dict] | None = None,
) -> CheckpointResult:
    """Grade one checkpoint. Returns a CheckpointResult with partial credit.

    Args:
        submission_images: Optional list of image dicts from file_parser_enhanced.
            Each dict has keys: base64 (str), media_type (str), source (str).
            When provided, the judge receives a multimodal message (text + images)
            so scanned PDFs, diagrams, and photo submissions are graded correctly.
            Images are capped at NVIDIA_MAX_IMAGES_PER_REQUEST per call.
    """
    # Flow-audit gap #1 fix: warn when content is truncated so this is never silent.
    content_limit = 28_000
    truncated = len(submission_content) > content_limit
    content_for_prompt = submission_content[:content_limit]
    if truncated:
        logger.warning(
            "Judge input truncated: %d → %d chars for checkpoint '%s' "
            "(student content beyond %d chars is NOT graded).",
            len(submission_content), content_limit, checkpoint_id, content_limit,
        )

    prompt = JUDGE_USER_TEMPLATE.format(
        title=title or "Assessment",
        criterion=criterion,
        points=points_max,
        pass_description=pass_description or f"Student clearly satisfies: {criterion}",
        fail_description=fail_description or f"Student does not address: {criterion}",
        content=content_for_prompt,
    )

    # Always use NVIDIA as primary — GLM (z-ai/glm4.7) returns empty strings for
    # prompts > ~5k chars, which causes silent grading failures.  NVIDIA Llama is
    # faster, more reliable, and handles any submission length correctly.
    nvidia_client = _get_nvidia_client()
    if nvidia_client is None:
        return _error_result(checkpoint_id, criterion, points_max, "No LLM client available", "none")

    client = nvidia_client
    model = NVIDIA_MODEL

    # Build the user message — multimodal when images are present, plain string otherwise.
    # An empty list is treated the same as None (no images).
    images_to_send = (submission_images or [])[:NVIDIA_MAX_IMAGES_PER_REQUEST]

    def _build_user_message(current_prompt: str) -> dict:
        if images_to_send:
            content: list[dict] = [{"type": "text", "text": current_prompt}]
            for img in images_to_send:
                content.append({
                    "type": "text",
                    "text": f"\n[Visual content — image from: {img.get('source', 'file')}]",
                })
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{img.get('media_type', 'image/png')};base64,{img['base64']}",
                        "detail": "high",
                    },
                })
            return {"role": "user", "content": content}
        return {"role": "user", "content": current_prompt}

    user_message = _build_user_message(prompt)

    # Acquire rate limit slot
    if rate_limiter is not None:
        await rate_limiter()

    raw = ""
    last_parse_error = None
    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                    user_message,
                ],
                temperature=0,
                seed=42,       # H-2 fix: deterministic output — same student always gets same grade
                max_tokens=800,
            )
            raw = _safe_content(response)
            if not raw:
                # Empty response (or empty choices) — retry with smaller content
                logger.warning("Judge attempt %d returned empty response, retrying with shorter content", attempt + 1)
                prompt = prompt[:len(prompt) * 3 // 4]  # shrink text by 25%; images stay
                user_message = _build_user_message(prompt)
                continue

            # Try to parse now — retry if JSON is invalid
            result = _parse_judge_response(raw, checkpoint_id, criterion, points_max, model)
            if result.needs_review and any("grading_error" in f for f in result.flags):
                last_parse_error = result
                logger.warning(
                    "Judge attempt %d returned invalid JSON (attempt %d/3), retrying",
                    attempt + 1, attempt + 1,
                )
                # Add explicit reminder to return valid JSON on retry
                reminder = "\n\nIMPORTANT: Your previous response was not valid JSON. You MUST respond with ONLY a valid JSON object. No explanations, no markdown, no extra text."
                user_message = _build_user_message(prompt + reminder)
                raw = ""
                continue

            result.judge_truncated = truncated
            return result

        except Exception as e:
            logger.warning("Judge attempt %d failed: %s", attempt + 1, e)
            if attempt == 2:
                err = _error_result(checkpoint_id, criterion, points_max, str(e), model)
                err.judge_truncated = truncated
                return err

    if not raw:
        err = _error_result(checkpoint_id, criterion, points_max, "LLM returned empty response after retries", model)
        err.judge_truncated = truncated
        return err

    # If we exhausted retries on JSON parse error, return the last parse error result
    if last_parse_error is not None:
        last_parse_error.judge_truncated = truncated
        return last_parse_error

    result = _parse_judge_response(raw, checkpoint_id, criterion, points_max, model)
    result.judge_truncated = truncated
    return result


def _parse_judge_response(
    raw: str,
    checkpoint_id: str,
    criterion: str,
    points_max: float,
    model: str,
) -> CheckpointResult:
    """Parse JSON from judge, return CheckpointResult."""
    # Strip markdown fences if present
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()

    # Try to find JSON object
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(0)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.error("Judge returned invalid JSON: %s", raw[:300])
        return _error_result(checkpoint_id, criterion, points_max, "Invalid JSON response", model)

    # Parse score_percent — only allow valid values
    raw_pct = data.get("score_percent", 0)
    try:
        raw_pct = int(raw_pct)
    except (TypeError, ValueError):
        raw_pct = 0

    # Snap to nearest valid tier
    valid_tiers = [0, 25, 50, 75, 100]
    score_percent = min(valid_tiers, key=lambda t: abs(t - raw_pct))

    points_awarded = round(points_max * score_percent / 100, 2)

    confidence = str(data.get("confidence", "medium")).lower()
    if confidence not in ("high", "medium", "low"):
        confidence = "medium"

    return CheckpointResult(
        checkpoint_id=checkpoint_id,
        criterion=criterion,
        points_max=points_max,
        score_percent=score_percent,
        points_awarded=points_awarded,
        reasoning=str(data.get("reasoning", "")).strip(),
        evidence_quote=str(data.get("evidence_quote", "")).strip(),
        source_file=str(data.get("source_file", "unknown")).strip(),
        confidence=confidence,
        model_used=model,
    )


def _error_result(
    checkpoint_id: str,
    criterion: str,
    points_max: float,
    error_msg: str,
    model: str,
) -> CheckpointResult:
    return CheckpointResult(
        checkpoint_id=checkpoint_id,
        criterion=criterion,
        points_max=points_max,
        score_percent=0,
        points_awarded=0.0,
        reasoning=f"Grading error: {error_msg}",
        evidence_quote="",
        confidence="low",
        needs_review=True,
        flags=[f"grading_error: {error_msg}"],
        model_used=model,
    )
