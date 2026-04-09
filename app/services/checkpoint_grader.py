"""Checkpoint-based grading system.

Instead of asking the LLM to assign subjective numeric scores, this system:
1. Generates binary checkpoints from rubric criteria (one-time per rubric)
2. Asks the LLM to evaluate each checkpoint with EXACT evidence quotes
3. Verifies evidence quotes actually exist in the student's submission
4. Computes scores deterministically from verified checkpoints
5. Flags unverifiable results for teacher review

This eliminates the core inconsistency problem: same evidence always produces
the same score, because scoring is done by deterministic code — not the LLM.
"""

import json
import re
import logging
import hashlib
from difflib import SequenceMatcher
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# CHECKPOINT GENERATION
# ═══════════════════════════════════════════════════════════════

CHECKPOINT_GENERATION_PROMPT = """You are generating grading checkpoints for an academic assessment.

For each rubric criterion, create 3-7 specific, observable checkpoints that a grader can verify by looking at the student's submission.

RULES FOR GOOD CHECKPOINTS:
1. Each checkpoint must be BINARY (pass/fail) — no partial credit within a single checkpoint.
2. Each checkpoint must be OBSERVABLE — you can determine pass/fail by looking at the submission.
3. Checkpoints must be APPROACH-AGNOSTIC where possible — don't assume one specific solution method.
   Example: Instead of "Uses deque for BFS queue", write "Implements a queue data structure for BFS (deque, list, or equivalent)".
4. Checkpoint points MUST sum EXACTLY to the criterion's max marks.
5. Distribute points so that the most important aspects get the most points.
6. For code criteria: focus on LOGIC and CORRECTNESS, not syntax. Minor syntax errors should NOT cause checkpoint failure unless the checkpoint specifically checks syntax.
7. For theory/essay criteria: focus on KEY CONCEPTS and REASONING.
8. For math criteria: focus on METHOD and STEPS.

ALSO generate verification_keywords for each checkpoint — these are regex patterns
that SHOULD appear in the student's evidence if the checkpoint passes.
- For code: patterns like "while|for", "deque|queue|list", "append|push"
- For text: key terms that should appear like "BFS|breadth.first", "complexity|O\\("
- Use | for alternatives, \\b for word boundaries
- These are HINTS for automated verification, not strict requirements.

OUTPUT FORMAT (strict JSON):
{
  "criteria_checkpoints": [
    {
      "criterion": "<EXACT criterion name from rubric>",
      "max": <exact max marks>,
      "checkpoints": [
        {
          "id": "<criterion_slug>_cp<N>",
          "description": "<what to check — specific and observable>",
          "points": <integer points for this checkpoint>,
          "verification_keywords": ["regex_pattern1", "regex_pattern2"],
          "requires_visual": false
        }
      ]
    }
  ]
}

Set requires_visual=true ONLY for checkpoints that REQUIRE seeing an image/diagram
(e.g., "Student draws a correct network diagram"). Text-based checkpoints are always false.

ASSIGNMENT CONTEXT:
"""


async def generate_checkpoints(
    criteria: list[dict],
    assignment_description: str,
    questions: Optional[list[dict]] = None,
    _llm_call_fn=None,
    _rate_limiter=None,
) -> dict[str, list[dict]]:
    """Generate grading checkpoints for each rubric criterion.

    Args:
        criteria: Parsed rubric criteria [{"criterion": "...", "max": N, "description": "..."}]
        assignment_description: The assignment description text
        questions: Optional question structure
        _llm_call_fn: The LLM call function (injected to avoid circular imports)
        _rate_limiter: Rate limiter instance

    Returns:
        Dict mapping criterion name -> list of checkpoints
    """
    if not criteria or not _llm_call_fn:
        return {}

    # Build the prompt
    context_parts = [
        f"Assignment: {assignment_description or 'No description'}",
        "",
        "RUBRIC CRITERIA:",
    ]
    for c in criteria:
        name = c.get("criterion", "")
        max_marks = c.get("max", 0)
        desc = c.get("description", "")
        context_parts.append(f"  - \"{name}\": {max_marks} points")
        if desc:
            context_parts.append(f"    Description: {desc}")

    if questions:
        context_parts.append("")
        context_parts.append("QUESTION STRUCTURE:")
        for q in questions:
            q_id = q.get("id", q.get("label", "?"))
            q_desc = q.get("description", "")
            q_marks = q.get("marks", "?")
            context_parts.append(f"  {q_id} ({q_marks} marks): {q_desc}")
            for part in q.get("parts", []):
                p_id = part.get("id", part.get("label", "?"))
                p_desc = part.get("description", "")
                p_marks = part.get("marks", "?")
                context_parts.append(f"    {p_id} ({p_marks} marks): {p_desc}")

    user_prompt = CHECKPOINT_GENERATION_PROMPT + "\n".join(context_parts)

    try:
        if _rate_limiter:
            await _rate_limiter.acquire()

        response, meta = _llm_call_fn(
            purpose="checkpoint_generation",
            needs_vision=False,
            messages=[
                {"role": "system", "content": "You generate precise grading checkpoints for academic assessments. Output ONLY valid JSON."},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
            top_p=1.0,
            max_tokens=4000,
            seed=42,
            response_format={"type": "json_object"},
        )

        raw = ""
        if response and response.choices:
            raw = (response.choices[0].message.content or "").strip()
        if not raw:
            logger.warning("Checkpoint generation got empty API response — returning empty")
            return {}

        # Import json extraction from ai_grader_fixed
        from app.services.ai_grader_fixed import _extract_json
        parsed = _extract_json(raw)

        if not isinstance(parsed, dict):
            logger.error("Checkpoint generation returned non-dict: %s", type(parsed))
            return {}

        result: dict[str, list[dict]] = {}
        for crit_data in parsed.get("criteria_checkpoints", []):
            if not isinstance(crit_data, dict):
                continue
            crit_name = str(crit_data.get("criterion", "")).strip()
            if not crit_name:
                continue
            crit_max = float(crit_data.get("max", 0))
            checkpoints = []
            for cp in crit_data.get("checkpoints", []):
                if not isinstance(cp, dict):
                    continue
                checkpoints.append({
                    "id": str(cp.get("id", "")),
                    "description": str(cp.get("description", "")),
                    "points": int(round(float(cp.get("points", 0)))),
                    "verification_keywords": [
                        str(k) for k in cp.get("verification_keywords", [])
                        if isinstance(k, str)
                    ],
                    "requires_visual": bool(cp.get("requires_visual", False)),
                })

            # Validate: points must sum to criterion max
            total_points = sum(c["points"] for c in checkpoints)
            if checkpoints and (total_points != crit_max or total_points == 0):
                # Fix distribution — scale proportionally if LLM gave non-zero values
                if total_points > 0:
                    factor = crit_max / total_points
                    for cp in checkpoints:
                        cp["points"] = round(cp["points"] * factor, 2)
                else:
                    # All points are 0 (LLM rounded small fractions to 0)
                    # Distribute criterion max equally among checkpoints
                    each = round(crit_max / len(checkpoints), 2)
                    for cp in checkpoints:
                        cp["points"] = each
                # Fix rounding remainder on last checkpoint so total == crit_max exactly
                new_total = round(sum(c["points"] for c in checkpoints), 4)
                if abs(new_total - crit_max) > 0.001 and checkpoints:
                    checkpoints[-1]["points"] = round(
                        checkpoints[-1]["points"] + (crit_max - new_total), 2
                    )

            if checkpoints:
                result[crit_name] = checkpoints

        logger.info(
            "Generated checkpoints for %d/%d criteria",
            len(result), len(criteria),
        )
        return result

    except Exception as e:
        logger.error("Checkpoint generation failed: %s", e, exc_info=True)
        return {}


# ═══════════════════════════════════════════════════════════════
# CHECKPOINT GRADING PROMPT
# ═══════════════════════════════════════════════════════════════

CHECKPOINT_SYSTEM_PROMPT = """You are a factual evidence extractor for academic grading. Your job is to find and quote EXACT evidence from the student's submission for each grading checkpoint.

═══════════════════════════════════════════════════════════════
ABSOLUTE RULES — VIOLATING ANY OF THESE INVALIDATES YOUR RESPONSE:
═══════════════════════════════════════════════════════════════

RULE 1 — EXACT QUOTES ONLY:
  Your evidence_quote MUST be text COPIED EXACTLY from the student's submission.
  Do NOT paraphrase, summarize, correct, or improve what the student wrote.
  If they wrote "from collection import deque" (wrong), quote it EXACTLY as-is.
  NEVER write what the code SHOULD be — write what it IS.

RULE 2 — NO FABRICATION:
  If you cannot find evidence for a checkpoint, set:
    "pass": false, "evidence_quote": ""
  NEVER invent or imagine evidence that doesn't exist in the submission.
  NEVER combine text from different parts into one quote.

RULE 3 — GRADE THE ANSWER, NOT THE SETUP:
  Restating the problem is NOT solving it. Only grade actual solution work.
  Declaring given data or writing imports alone is NOT the answer.

RULE 4 — INCLUDE SOURCE FILE:
  Always specify which file the evidence comes from in source_file.
  If evidence spans multiple files, quote from the PRIMARY file.

RULE 5 — BINARY DECISIONS:
  Each checkpoint is strictly pass or fail. No partial credit within a checkpoint.
  "pass": true = the student clearly satisfies this checkpoint based on evidence.
  "pass": false = insufficient evidence, or the work doesn't meet the checkpoint.

RULE 6 — FAIR EVALUATION:
  For code: focus on LOGIC and whether it WORKS, not minor syntax issues
  (unless the checkpoint specifically checks syntax).
  Minor typos in otherwise correct logic should not fail a logic checkpoint.

  For text/essays: focus on whether the KEY CONCEPT is addressed.
  For math: focus on whether the correct METHOD is used.

═══════════════════════════════════════════════════════════════
FOR IMAGE-BASED CONTENT:
═══════════════════════════════════════════════════════════════
If the evidence comes from an image (screenshot, scan, diagram):
  - Set source_file to the image filename
  - Set evidence_quote to a description of what you SEE in the image
  - Prefix the quote with "[VISUAL] " to indicate it's from visual inspection
  - For visual-only checkpoints (diagrams, graphs), describe what is visible

═══════════════════════════════════════════════════════════════

RESPONSE FORMAT — return ONLY valid JSON:
{
  "checkpoint_evaluations": [
    {
      "checkpoint_id": "<id from the checkpoint list>",
      "pass": true/false,
      "evidence_quote": "<EXACT text copied from submission, or empty string if none>",
      "source_file": "<filename where evidence was found>",
      "reasoning": "<1-2 sentence explanation of your decision>"
    }
  ],
  "overall_feedback": "<comprehensive feedback about the submission>",
  "strengths": ["<specific strength>"],
  "weaknesses": ["<specific weakness>"],
  "suggestions_for_improvement": "<actionable advice>",
  "confidence": "high|medium|low",
  "confidence_reasoning": "<why>"
}
"""


def build_checkpoint_grading_prompt(
    checkpoints_by_criterion: dict[str, list[dict]],
    criteria: list[dict],
    student_content: str,
    student_files: list,
    title: str,
    description: str,
    max_score: int,
    vision_notes: Optional[str] = None,
    reference_solution: Optional[str] = None,
) -> str:
    """Build the user prompt for checkpoint-based grading."""
    parts = [
        f"ASSIGNMENT: {title}",
        f"MAX SCORE: {max_score} points",
        "",
        "DESCRIPTION:",
        description or "No description",
        "",
    ]

    # Checkpoints to evaluate
    parts.append("=" * 60)
    parts.append("CHECKPOINTS TO EVALUATE:")
    parts.append("For each checkpoint, find evidence in the submission and determine pass/fail.")
    parts.append("=" * 60)
    parts.append("")

    all_checkpoints = []
    for crit in criteria:
        crit_name = crit.get("criterion", "")
        crit_max = crit.get("max", 0)
        cps = checkpoints_by_criterion.get(crit_name, [])
        if not cps:
            continue

        parts.append(f"CRITERION: \"{crit_name}\" ({crit_max} points)")
        parts.append("-" * 40)
        for cp in cps:
            cp_id = cp.get("id", "")
            cp_desc = cp.get("description", "")
            cp_points = cp.get("points", 0)
            cp_visual = cp.get("requires_visual", False)
            visual_tag = " [REQUIRES VISUAL INSPECTION]" if cp_visual else ""
            parts.append(f"  [{cp_id}] ({cp_points} pts) {cp_desc}{visual_tag}")
            all_checkpoints.append(cp)
        parts.append("")

    # File content manifest
    parts.append("=" * 60)
    parts.append("STUDENT SUBMISSION FILES:")
    parts.append("=" * 60)
    empty_files = []
    for i, f in enumerate(student_files, 1):
        if hasattr(f, "filename"):
            fn = f.filename
            ft = f.file_type
            img_count = len(f.images) if f.images else 0
            text_content = f.text_content or ""
        elif isinstance(f, dict):
            fn = f.get("filename", "unknown")
            ft = f.get("file_type", f.get("type", "unknown"))
            img_count = len(f.get("images", []))
            text_content = f.get("text_content", "") or ""
        else:
            fn = f"file_{i}"
            ft = "unknown"
            img_count = 0
            text_content = ""

        text_len = len(text_content)
        if text_len == 0 and img_count == 0:
            empty_files.append(fn)
        status = "EMPTY" if text_len == 0 and img_count == 0 else f"{text_len} chars"
        if img_count > 0:
            status += f", {img_count} images"
        parts.append(f"  {i}. {fn} ({ft}) — {status}")
        if text_content.strip():
            preview = text_content.strip()[:200].replace("\n", " ")
            parts.append(f"     Preview: {preview}...")

    if empty_files:
        parts.append(f"\n⚠ EMPTY FILES (score 0 for related checkpoints): {', '.join(empty_files)}")
    parts.append("")

    if vision_notes:
        parts.append("VISION PRE-ANALYSIS NOTES (from image transcription):")
        parts.append(vision_notes[:8000])
        parts.append("")

    if reference_solution:
        parts.append("REFERENCE SOLUTION (for comparison):")
        parts.append(str(reference_solution)[:15000])
        parts.append("")

    parts.append("IMPORTANT: Your evidence_quote MUST be EXACT text from the submission files above.")
    parts.append("For each checkpoint_id listed above, provide an evaluation in your response.")

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════
# EVIDENCE VERIFICATION (deterministic, no LLM)
# ═══════════════════════════════════════════════════════════════

def _normalize_text(text: str) -> str:
    """Normalize text for comparison — collapse whitespace, strip."""
    return " ".join(text.split()).strip()


def _strip_to_chars(text: str) -> str:
    """Strip ALL whitespace from text, keeping only non-whitespace characters.

    This is the core of our evidence matching strategy:
    - Student writes: 'int  a = 3\\n   def fun():\\n         return n'
    - Becomes:        'inta=3deffun():returnn'
    - LLM quotes:     'int a=3 def fun(): return n'
    - Becomes:        'inta=3deffun():returnn'
    - EXACT MATCH!

    By removing all whitespace, we eliminate the #1 source of false negatives
    (LLM reformatting whitespace when quoting student code/text).
    """
    import re
    return re.sub(r'\s+', '', text)


def verify_evidence_exists(
    quote: str,
    full_content: str,
    threshold: float = 0.75,
) -> tuple[bool, float, str]:
    """Verify that an evidence quote exists in the student's submission.

    Uses space-stripped matching as the primary method:
    - Strip ALL whitespace from both quote and content
    - Find the stripped quote as a substring of stripped content
    - Use SequenceMatcher for partial/fuzzy matching when exact fails

    Returns:
        (found, similarity_score, match_method)

    Similarity tiers:
        >= 0.80: GREEN — verified, award full points
        0.40-0.79: YELLOW — likely present, award points but flag for review
        < 0.20: RED — fabricated, override to 0 points
        0.20-0.39: ORANGE — uncertain, award points but strongly flag
    """
    if not quote or not quote.strip():
        return (False, 0.0, "empty_quote")

    # Skip verification for visual evidence (prefixed with [VISUAL])
    stripped_quote = quote.strip()
    if stripped_quote.startswith("[VISUAL]"):
        return (True, 1.0, "visual_evidence")

    # ── PRIMARY METHOD: Space-stripped matching ───────────────────
    # Strip all whitespace from both texts
    chars_quote = _strip_to_chars(quote).lower()
    chars_content = _strip_to_chars(full_content).lower()

    if not chars_quote or not chars_content:
        return (False, 0.0, "empty_after_strip")

    # Fast path: exact substring match on stripped chars
    if chars_quote in chars_content:
        return (True, 1.0, "stripped_exact")

    # Sliding window on stripped chars for fuzzy matching
    # Find the best-matching window of similar length
    q_len = len(chars_quote)
    best_ratio = 0.0
    best_method = "stripped_fuzzy"

    if q_len <= 10:
        # Very short evidence — check if it appears anywhere
        # Use a wider window
        step = 1
        window_size = min(q_len + 5, len(chars_content))
    else:
        step = max(1, q_len // 4)
        window_size = q_len

    for i in range(0, max(1, len(chars_content) - window_size + 1), step):
        window = chars_content[i : i + window_size + q_len // 3]  # slightly wider window
        ratio = SequenceMatcher(None, chars_quote, window).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            if ratio >= 0.95:
                return (True, ratio, "stripped_exact_fuzzy")

    if best_ratio >= 0.80:
        return (True, best_ratio, "stripped_high_match")
    if best_ratio >= 0.40:
        return (True, best_ratio, "stripped_partial_match")
    if best_ratio >= 0.20:
        return (False, best_ratio, "stripped_uncertain")

    # ── FALLBACK: Traditional normalized matching ─────────────────
    # (catches cases where space-stripping doesn't help)
    norm_quote = _normalize_text(quote)
    norm_content = _normalize_text(full_content)

    if norm_quote and norm_content:
        if norm_quote in norm_content:
            return (True, 1.0, "exact_substring")
        if norm_quote.lower() in norm_content.lower():
            return (True, 0.98, "case_insensitive_substring")

    return (False, best_ratio, "no_match")


def verify_checkpoint_keywords(
    checkpoint: dict,
    evidence_quote: str,
    verdict: bool,
) -> tuple[bool, Optional[str]]:
    """Verify that evidence contains expected keywords for a passing checkpoint.

    Returns:
        (consistent, inconsistency_reason)
    """
    keywords = checkpoint.get("verification_keywords", [])
    if not keywords or not verdict:
        # No keywords to check, or checkpoint failed (no need to verify keywords)
        return (True, None)

    if not evidence_quote or evidence_quote.strip().startswith("[VISUAL]"):
        # No text evidence or visual evidence — can't verify keywords
        return (True, None)  # Don't flag, might be legitimate

    # Check each keyword pattern against the evidence
    missing_patterns = []
    for pattern in keywords:
        try:
            if not re.search(pattern, evidence_quote, re.IGNORECASE):
                missing_patterns.append(pattern)
        except re.error:
            # Bad regex — skip it
            continue

    if missing_patterns and len(missing_patterns) == len(keywords):
        # ALL keywords missing — likely a hallucination
        return (
            False,
            f"Evidence lacks ALL expected patterns: {', '.join(missing_patterns[:3])}",
        )

    # Some keywords present — probably fine
    return (True, None)


def check_cross_checkpoint_consistency(
    checkpoint_results: list[dict],
    checkpoints: list[dict],
) -> list[str]:
    """Check for logical contradictions between checkpoint results.

    Returns list of inconsistency descriptions.
    """
    inconsistencies = []

    # Build lookup
    result_by_id = {r["checkpoint_id"]: r for r in checkpoint_results}
    cp_by_id = {cp["id"]: cp for cp in checkpoints}

    # Check depends_on relationships
    for cp in checkpoints:
        cp_id = cp["id"]
        depends_on = cp.get("depends_on", [])
        result = result_by_id.get(cp_id)
        if not result or not result.get("pass"):
            continue

        # This checkpoint passes — check if its dependencies also pass
        for dep_id in depends_on:
            dep_result = result_by_id.get(dep_id)
            if dep_result and not dep_result.get("pass"):
                dep_cp = cp_by_id.get(dep_id, {})
                inconsistencies.append(
                    f"'{cp.get('description', cp_id)}' passes but depends on "
                    f"'{dep_cp.get('description', dep_id)}' which fails"
                )

    return inconsistencies


# ═══════════════════════════════════════════════════════════════
# DETERMINISTIC SCORING
# ═══════════════════════════════════════════════════════════════

def score_criterion_from_checkpoints(
    checkpoints: list[dict],
    results: list[dict],
) -> dict:
    """Compute a deterministic score for a criterion from checkpoint results.

    Scoring rules (designed for CONSISTENCY — same work = same score):
    - Pass + verified evidence         → Full points (confident)
    - Pass + unverified but has evidence → Full points (flagged for review)
    - Pass + NO evidence AND retries exhausted → 0 points (clear fabrication)
    - Pass + NO evidence (empty quote)  → 0 points (fabrication)
    - Fail                              → 0 points

    Verification determines FLAGS/CONFIDENCE, not score — except for clear
    fabrication (empty evidence on a pass). This prevents the LLM's quoting
    style from causing score differences between identical submissions.

    Returns:
        {
            "score": float,
            "max": float,
            "scored_checkpoints": [{"id": ..., "pass": bool, "points": int, "points_awarded": int}],
        }
    """
    result_by_id = {r["checkpoint_id"]: r for r in results}
    max_points = sum(cp.get("points", 0) for cp in checkpoints)
    total_awarded = 0
    scored = []

    for cp in checkpoints:
        cp_id = cp["id"]
        cp_points = cp.get("points", 0)
        result = result_by_id.get(cp_id)

        if not result or not result.get("pass"):
            # Fail or not evaluated → 0 points
            awarded = 0
        elif _is_clear_fabrication(result):
            # Pass with clear fabrication → 0 points
            awarded = 0
        else:
            # Pass with some evidence (verified or not) → full points
            awarded = cp_points

        total_awarded += awarded
        tier = get_evidence_flag_tier(result) if result else "none"
        scored.append({
            "id": cp_id,
            "pass": bool(result and result.get("pass")),
            "verified": bool(result and result.get("verified", False)),
            "points": cp_points,
            "points_awarded": awarded,
            "evidence_tier": tier,
            "evidence_similarity": result.get("evidence_similarity", 0.0) if result else 0.0,
        })

    return {
        "score": float(total_awarded),
        "max": float(max_points),
        "scored_checkpoints": scored,
    }


def _is_clear_fabrication(result: dict) -> bool:
    """Determine if a passing checkpoint result is a clear fabrication.

    Uses tiered similarity scoring:
    - >= 0.80: GREEN — verified, definitely exists
    - 0.40-0.79: YELLOW — probably exists, flag for review
    - 0.20-0.39: ORANGE — uncertain, flag strongly
    - < 0.20 with evidence: RED — likely fabricated, override to 0
    - Empty evidence: RED — definitely fabricated

    Returns True only for RED tier (clear fabrication).
    """
    evidence = str(result.get("evidence_quote", "") or "").strip()

    # Case 1: Pass with completely empty evidence → fabrication
    if not evidence:
        return True

    # Case 2: Visual evidence is never fabrication (can't verify text)
    if evidence.startswith("[VISUAL]"):
        return False

    # Case 3: Check evidence_similarity score if available
    # The verify_evidence_exists function sets this
    similarity = result.get("evidence_similarity", None)
    if similarity is not None and similarity < 0.20:
        # RED tier: very low similarity = fabricated evidence
        return True

    # Case 4: Retries exhausted with NO evidence at all
    reasoning = str(result.get("reasoning", "") or "")
    if "no retry response received" in reasoning and not evidence:
        return True

    # Everything else: evidence exists (verified or not), award points.
    # Flag for review based on tier, but don't reduce score.
    return False


def get_evidence_flag_tier(result: dict) -> str:
    """Return the verification flag tier for display in the frontend.

    Returns:
        'green'  — verified (similarity >= 0.80)
        'yellow' — partial match (0.40-0.79), award points but flag
        'orange' — uncertain (0.20-0.39), award points but strongly flag
        'red'    — fabricated (< 0.20 or empty), 0 points
        'visual' — visual evidence, can't verify text
        'none'   — not applicable (fail, not evaluated)
    """
    if not result or not result.get("pass"):
        return "none"

    evidence = str(result.get("evidence_quote", "") or "").strip()
    if not evidence:
        return "red"

    if evidence.startswith("[VISUAL]"):
        return "visual"

    if result.get("verified"):
        return "green"

    similarity = result.get("evidence_similarity", 0.0)
    if similarity >= 0.80:
        return "green"
    elif similarity >= 0.40:
        return "yellow"
    elif similarity >= 0.20:
        return "orange"
    else:
        return "red"


# ═══════════════════════════════════════════════════════════════
# FLAGGING LOGIC
# ═══════════════════════════════════════════════════════════════

def compute_criterion_flags(
    criterion_name: str,
    checkpoints: list[dict],
    results: list[dict],
    has_visual_content: bool = False,
) -> list[str]:
    """Compute flags for a criterion based on verification results.

    Uses tier-based flagging:
    - RED: Serious concern (empty evidence or very low similarity)
    - ORANGE: Low confidence (might need review)
    - YELLOW/GREEN: Normal - no flags needed
    """
    flags = []
    result_by_id = {r.get("checkpoint_id", ""): r for r in results}

    low_confidence_count = 0
    total_checkpoints = len(checkpoints)
    evaluated_checkpoints = 0

    for cp in checkpoints:
        cp_id = cp["id"]
        result = result_by_id.get(cp_id)

        if not result:
            flags.append(f"Checkpoint not evaluated: {cp.get('description', cp_id)}")
            continue

        evaluated_checkpoints += 1

        if not result.get("pass"):
            continue  # Fails don't need verification flags

        # Use tier system for flagging
        tier = get_evidence_flag_tier(result)
        desc = cp.get('description', cp_id)

        if tier == "red":
            flags.append(f"Evidence could not be verified for: {desc}")
        elif tier == "orange":
            low_confidence_count += 1

    # Summary flag for low confidence checkpoints
    if low_confidence_count > 0:
        flags.append(
            f"{low_confidence_count} checkpoint(s) have low verification confidence"
        )

    # Flag visual content only if visual checkpoints exist
    if has_visual_content and any(cp.get("requires_visual") for cp in checkpoints):
        visual_count = sum(1 for cp in checkpoints if cp.get("requires_visual"))
        flags.append(f"Contains visual content requiring manual review ({visual_count} visual checkpoint(s))")

    # Flag if not all checkpoints were evaluated
    if evaluated_checkpoints < total_checkpoints:
        missing = total_checkpoints - evaluated_checkpoints
        flags.append(f"{missing} checkpoint(s) not evaluated by AI")

    return flags


# ═══════════════════════════════════════════════════════════════
# COLLECT FULL STUDENT TEXT (for evidence verification)
# ═══════════════════════════════════════════════════════════════

def collect_student_text(student_files: list, vision_notes: Optional[str] = None) -> str:
    """Collect all text content from student files into a single searchable string.

    Includes both file text content and vision transcription notes.
    Logs a warning when total content significantly exceeds the judge's 28K window
    so operators are aware of the truncation that will occur.
    """
    parts = []
    total_chars = 0

    for f in student_files:
        if hasattr(f, "filename"):
            fn = getattr(f, "filename", "") or "unknown"
            text = getattr(f, "text_content", "") or ""
        elif isinstance(f, dict):
            fn = f.get("filename", "") or "unknown"
            text = f.get("text_content", "") or ""
        else:
            continue

        # Ensure text is always a string (guard against bytes or None)
        if not isinstance(text, str):
            text = text.decode("utf-8", errors="replace") if isinstance(text, bytes) else str(text)

        if text.strip():
            section = f"--- FILE: {fn} ---\n{text}"
            parts.append(section)
            total_chars += len(section)

    if vision_notes:
        parts.append(f"--- VISION NOTES ---\n{vision_notes}")
        total_chars += len(vision_notes)

    result = "\n\n".join(parts)

    # Warn when content far exceeds the 60K judge window
    if total_chars > 62_000:
        logger.warning(
            "Student submission text is %d chars; judge input is capped at 60K "
            "— %d chars (~%.0f%%) will be truncated. "
            "Content beyond 60K will not be graded for this student.",
            total_chars,
            max(0, total_chars - 60_000),
            max(0, total_chars - 60_000) / total_chars * 100 if total_chars else 0,
        )

    if not result.strip():
        logger.warning(
            "collect_student_text: no text content found in any of %d student files",
            len(student_files),
        )
        result = "[EMPTY SUBMISSION — no text content found in any submitted file]"

    return result


# ═══════════════════════════════════════════════════════════════
# FILE FILTERING — remove system / hidden / IDE files
# ═══════════════════════════════════════════════════════════════

# Exact basenames (lower-cased) that are always noise
_HIDDEN_EXACT_NAMES: frozenset[str] = frozenset({
    ".ds_store", "thumbs.db", "desktop.ini", "ehthumbs.db",
    ".gitignore", ".gitattributes", ".gitmodules", ".gitkeep",
    "pipfile.lock", "package-lock.json", "yarn.lock", "poetry.lock",
    "composer.lock", "gemfile.lock", "cargo.lock",
    ".env", ".env.local", ".env.example", ".env.test",
    ".editorconfig", ".prettierrc", ".eslintrc", ".babelrc",
    ".stylelintrc", "jest.config.js", "babel.config.js",
    "setup.cfg", "tox.ini", "mypy.ini", ".flake8",
    "makefile", "dockerfile", ".dockerignore",
    ".travis.yml", ".circleci", "appveyor.yml",
})

# Path segments — if ANY path component matches (after stripping leading dot),
# the file is considered a system/IDE artifact and is removed.
# Store both the raw and dot-stripped versions for flexible matching.
_HIDDEN_PATH_SEGMENTS: frozenset[str] = frozenset({
    # Stored WITHOUT leading dot so we can match .vscode/ and vscode/ alike
    "__macosx",      # macOS zip artifact directory
    "git",           # .git directory
    "svn",           # .svn directory
    "hg",            # .hg directory
    "vscode",        # .vscode settings
    "idea",          # .idea JetBrains settings
    "eclipse",       # .eclipse settings
    "__pycache__",   # Python bytecode cache
    "node_modules",  # Node.js dependencies
    "next",          # .next Next.js build cache
    "tox",           # .tox environments
    "pytest_cache",  # .pytest_cache
    "mypy_cache",    # .mypy_cache
    "ruff_cache",    # .ruff_cache
    "hypothesis",    # .hypothesis testing cache
    "benchmarks",    # .benchmarks
    # Note: ".egg-info" is handled by extension / substring matching below
})

# Extensions that are always compiled/binary artifacts or logs
_HIDDEN_EXTENSIONS: frozenset[str] = frozenset({
    ".pyc", ".pyo", ".pyd",           # Python compiled
    ".class",                          # Java compiled
    ".o", ".obj", ".a",               # C/C++ object files
    ".so", ".dll", ".dylib",          # Shared libraries
    ".exe", ".bin",                    # Executables
    ".log",                            # Log files
    ".tmp", ".temp",                   # Temp files
    ".bak", ".backup",                 # Backup files
    ".swp", ".swo", ".swn",           # Vim swap files
    ".orig",                           # Merge originals
    ".rej",                            # Patch rejects
    ".lock",                           # Lock files
    ".iml",                            # IntelliJ module files
    ".ipr", ".iws",                    # IntelliJ project files
})

# Filename prefixes (on the basename) that indicate hidden/system files
_HIDDEN_BASENAME_PREFIXES: tuple[str, ...] = (
    "._",    # macOS resource forks
    ".~",    # LibreOffice / temp files
    "~$",    # MS Office temp files
)


def filter_submission_files(student_files: list) -> list:
    """Remove hidden, system, IDE and OS-generated files before grading.

    These files carry no submission content but can confuse the LLM router and
    grader, increase token usage, and in rare cases cause false-positive
    evidence quotes (e.g. a .DS_Store binary being quoted as "code").

    Safe to call on any list of file objects or dicts — unknown types are kept.
    Files that have no text content but are not system files are also kept
    (they may be image-only PDFs or similar).

    Returns a new list; the input is not mutated.
    """
    filtered: list = []
    removed: list[str] = []

    for f in student_files:
        if hasattr(f, "filename"):
            fn = getattr(f, "filename", "") or ""
        elif isinstance(f, dict):
            fn = f.get("filename", "") or ""
        else:
            # Unknown type — keep to be safe
            filtered.append(f)
            continue

        if not isinstance(fn, str):
            fn = str(fn) if fn else ""

        # Normalise path separators for cross-platform comparison
        fn_norm = fn.replace("\\", "/").lower()
        fn_base = fn_norm.split("/")[-1]          # just the filename part
        fn_segments = fn_norm.split("/")           # all path components

        # ── 1. Exact filename match ────────────────────────────────
        if fn_base in _HIDDEN_EXACT_NAMES:
            removed.append(fn)
            continue

        # ── 2. Path segment match ──────────────────────────────────
        skip = False
        for seg in fn_segments:
            # Strip leading dot and trailing slashes for flexible comparison:
            #   ".vscode" → "vscode", "__pycache__" → "__pycache__"
            seg_clean = seg.lstrip(".").rstrip("/")
            if seg_clean in _HIDDEN_PATH_SEGMENTS:
                skip = True
                break
            # Also catch ".egg-info" style suffixes (e.g. mypackage.egg-info)
            if seg_clean.endswith(".egg-info") or seg_clean.endswith("-info"):
                skip = True
                break
        if skip:
            removed.append(fn)
            continue

        # ── 3. Extension match ─────────────────────────────────────
        if "." in fn_base:
            ext = "." + fn_base.rsplit(".", 1)[-1]
            if ext in _HIDDEN_EXTENSIONS:
                removed.append(fn)
                continue

        # ── 4. Basename prefix match ───────────────────────────────
        skip2 = False
        for pfx in _HIDDEN_BASENAME_PREFIXES:
            if fn_base.startswith(pfx):
                skip2 = True
                break
        if skip2:
            removed.append(fn)
            continue

        filtered.append(f)

    if removed:
        logger.info(
            "filter_submission_files: dropped %d system/hidden file(s): %s%s",
            len(removed),
            ", ".join(removed[:8]),
            " …" if len(removed) > 8 else "",
        )

    return filtered


# ═══════════════════════════════════════════════════════════════
# FILE ROUTING — assign files to rubric criteria via LLM
# ═══════════════════════════════════════════════════════════════

_ROUTING_SYSTEM_PROMPT = """You are a file router for an academic grading system.

Given a set of rubric criteria and a batch of student submission files, determine which files are relevant for evaluating each criterion.

RULES:
1. A file is relevant to a criterion if grading that criterion requires examining that file.
2. A single file can be relevant to many criteria (e.g. a file that contains all answers).
3. If a criterion has no matching files in THIS batch, return an empty list for it — other batches may cover it.
4. When uncertain, INCLUDE the file — missing a relevant file is worse than including an extra one.
5. Judge by CONTENT meaning, not just filename keywords. A file named "solution.py" may answer Q3 even if not named "q3.py".

Return ONLY valid JSON — no markdown, no explanation text."""

_ROUTING_USER_TEMPLATE = """RUBRIC CRITERIA (grade these):
{criteria_list}

FILES IN THIS BATCH:
{files_block}

Return JSON mapping each criterion to the relevant filenames from this batch:
{{
  "routing": {{
    "<exact criterion name>": ["<filename>", ...],
    "<exact criterion name>": [],
    ...
  }}
}}

Include EVERY criterion in the response (empty list if none of these files apply).
"""


async def route_files_to_criteria(
    criteria: list[dict],
    student_files: list,
    rate_limiter=None,
    files_per_batch: int = 5,
    preview_chars: int = 700,
) -> tuple[dict[str, list[str]], bool]:
    """Route student files to rubric criteria using LLM semantic understanding.

    Instead of heuristic Q-number filename matching, this sends the rubric and
    a preview of each file to the LLM which returns which files are relevant for
    each criterion.  Large submissions are handled by batching: we send
    ``files_per_batch`` files per API call and merge results across batches.

    Args:
        criteria:        Rubric criteria dicts — must have "criterion" key.
        student_files:   File objects/dicts with filename + text_content.
        rate_limiter:    Async callable to throttle API calls.
        files_per_batch: Number of files per routing call (default 5).
        preview_chars:   Characters of each file's content sent to router.

    Returns:
        dict[criterion_name, list[filename]] — merged across all batches.
        On total failure returns {} so callers fall back to full-text grading.
    """
    if not criteria or not student_files:
        return {}, False

    try:
        from app.config import NVIDIA_API_KEY, NVIDIA_BASE_URL, NVIDIA_MODEL
        from openai import OpenAI
        from app.services.ai_grader_fixed import _extract_json
    except Exception as e:
        logger.warning("route_files_to_criteria: import error %s — skipping routing", e)
        return {}, True

    # Build criteria label string (sent in every batch call)
    criteria_list_str = "\n".join(
        f'  - "{c.get("criterion", "")}" ({c.get("max", 0)} marks)'
        for c in criteria
        if c.get("criterion")
    )

    # Collect (filename, text_preview, images) for all files
    from app.config import MAX_IMAGES_PER_FILE_FOR_ROUTING
    file_infos: list[tuple[str, str, list[dict]]] = []
    for f in student_files:
        if hasattr(f, "filename"):
            fn = getattr(f, "filename", "") or "unknown"
            text = getattr(f, "text_content", "") or ""
            raw_images = list(getattr(f, "images", None) or [])
        elif isinstance(f, dict):
            fn = f.get("filename", "") or "unknown"
            text = f.get("text_content", "") or ""
            raw_images = list(f.get("images") or [])
        else:
            continue
        if not isinstance(text, str):
            text = text.decode("utf-8", errors="replace") if isinstance(text, bytes) else str(text) if text else ""

        # Cap images per file for routing (low detail — routing just needs to understand content type)
        route_images = [img for img in raw_images[:MAX_IMAGES_PER_FILE_FOR_ROUTING] if img.get("base64")]

        # For image-only files (no text) still tell the router the file exists
        if not text.strip():
            text = (
                "[IMAGE FILE — contains visual/handwritten content, see attached images]"
                if route_images
                else "[EMPTY FILE]"
            )

        file_infos.append((fn, text, route_images))

    if not file_infos:
        return {}

    # Accumulated routing: criterion → set of filenames
    all_crit_names = [c.get("criterion", "") for c in criteria if c.get("criterion")]
    routing_map: dict[str, set[str]] = {name: set() for name in all_crit_names}

    client = OpenAI(
        base_url=NVIDIA_BASE_URL,
        api_key=NVIDIA_API_KEY,
        timeout=60.0,
    )

    batch_count = 0
    fail_count = 0

    for batch_start in range(0, len(file_infos), files_per_batch):
        batch = file_infos[batch_start: batch_start + files_per_batch]
        batch_count += 1

        # Build the text file block for this batch (used by both paths)
        files_parts: list[str] = []
        for fn, text, _ in batch:
            preview = text[:preview_chars].strip()
            if len(text) > preview_chars:
                preview += f"\n[… {len(text) - preview_chars} more chars …]"
            files_parts.append(f"FILE: {fn}\n{'-' * 40}\n{preview}\n{'-' * 40}")
        files_block = "\n\n".join(files_parts)
        base_prompt = _ROUTING_USER_TEMPLATE.format(
            criteria_list=criteria_list_str,
            files_block=files_block,
        )

        # Build API message — multimodal when any file in the batch has images
        has_images_in_batch = any(imgs for _, _, imgs in batch)
        if has_images_in_batch:
            # Start with the full text prompt, then append image parts per file.
            # Using the fully-formatted base_prompt avoids fragile template string-splitting.
            user_content: list[dict] = [{"type": "text", "text": base_prompt}]
            for fn, _text, imgs in batch:
                for img in imgs:
                    user_content.append({"type": "text", "text": f"[Image from {fn}:]"})
                    user_content.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{img.get('media_type', 'image/png')};base64,{img['base64']}",
                            "detail": "low",  # routing only needs content type, not fine detail
                        },
                    })
            user_message: dict = {"role": "user", "content": user_content}
        else:
            # Text-only batch — plain string (no regression for existing submissions)
            user_message = {"role": "user", "content": base_prompt}

        if rate_limiter is not None:
            await rate_limiter()

        try:
            response = client.chat.completions.create(
                model=NVIDIA_MODEL,
                messages=[
                    {"role": "system", "content": _ROUTING_SYSTEM_PROMPT},
                    user_message,
                ],
                temperature=0.0,
                seed=42,
                max_tokens=2000,
            )
            raw = ""
            if response and response.choices:
                raw = (response.choices[0].message.content or "").strip()
            if not raw:
                logger.warning("route_files_to_criteria: batch %d got empty API response — skipping batch", batch_count)
                fail_count += 1
                continue
            parsed = _extract_json(raw)

            if isinstance(parsed, dict):
                routing = parsed.get("routing", {})
                if isinstance(routing, dict):
                    for crit_name, filenames in routing.items():
                        if crit_name in routing_map and isinstance(filenames, list):
                            for fn in filenames:
                                if isinstance(fn, str) and fn.strip():
                                    routing_map[crit_name].add(fn.strip())
                else:
                    logger.warning(
                        "route_files_to_criteria: batch %d — 'routing' key is not a dict: %s",
                        batch_count, type(routing),
                    )
                    fail_count += 1
            else:
                logger.warning(
                    "route_files_to_criteria: batch %d — could not parse JSON from: %s…",
                    batch_count, raw[:200],
                )
                fail_count += 1

        except Exception as e:
            logger.warning(
                "route_files_to_criteria: batch %d failed (%s) — skipping batch (files will be unrouted)",
                batch_count, e,
            )
            fail_count += 1
            # DO NOT map all files to all criteria — that poisons grading.
            # Unrouted files will be handled by the Q-number heuristic in orchestrator.

    # Convert sets to sorted lists
    result: dict[str, list[str]] = {k: sorted(v) for k, v in routing_map.items()}
    routing_fallback_occurred = fail_count > 0

    total_mappings = sum(len(v) for v in result.values())
    logger.info(
        "route_files_to_criteria: %d criteria × %d files → %d criterion-file mappings "
        "(%d batches, %d failures)",
        len(all_crit_names), len(file_infos), total_mappings, batch_count, fail_count,
    )

    return result, routing_fallback_occurred


# ═══════════════════════════════════════════════════════════════
# RETRY LOGIC FOR HALLUCINATED CHECKPOINTS
# ═══════════════════════════════════════════════════════════════

def build_retry_prompt_for_checkpoints(
    failed_checkpoints: list[dict],
    student_content_snippet: str,
) -> str:
    """Build a focused retry prompt for checkpoints that failed verification.

    Instead of re-grading everything, we only ask about the specific
    checkpoints that had evidence issues.
    """
    parts = [
        "Your previous evaluation for the following checkpoints could not be verified.",
        "The evidence you quoted was NOT FOUND in the student's submission.",
        "",
        "Please re-evaluate ONLY these checkpoints. Look VERY CAREFULLY at the submission text below.",
        "If you truly cannot find evidence, set pass=false and evidence_quote=\"\".",
        "",
        "CHECKPOINTS TO RE-EVALUATE:",
    ]
    for cp in failed_checkpoints:
        parts.append(f"  [{cp['id']}] ({cp['points']} pts) {cp['description']}")

    parts.append("")
    parts.append("RELEVANT SUBMISSION CONTENT:")
    parts.append(student_content_snippet[:10000])
    parts.append("")
    parts.append(
        "RESPONSE FORMAT: {\"checkpoint_evaluations\": ["
        "{\"checkpoint_id\": \"...\", \"pass\": true/false, "
        "\"evidence_quote\": \"<EXACT text or empty>\", "
        "\"source_file\": \"...\", \"reasoning\": \"...\"}]}"
    )

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════
# MAIN GRADING ENTRY POINT
# ═══════════════════════════════════════════════════════════════

async def grade_with_checkpoints(
    checkpoints_by_criterion: dict[str, list[dict]],
    criteria: list[dict],
    student_files: list,
    title: str,
    description: str,
    max_score: int,
    messages_with_images: list[dict],
    vision_notes: Optional[str] = None,
    reference_solution: Optional[str] = None,
    _llm_call_fn=None,
    _rate_limiter=None,
    _extract_json_fn=None,
    preferred_provider: str = "",
) -> dict[str, Any]:
    """Grade a student using checkpoint-based evaluation.

    This replaces the holistic scoring approach with:
    1. LLM evaluates each checkpoint (pass/fail + evidence quote)
    2. Evidence quotes verified against actual submission
    3. Scores computed deterministically from verified checkpoints
    4. Unverifiable results flagged for teacher review

    Returns the same format as grade_student for backward compatibility,
    plus additional checkpoint data under each criterion.
    """
    import asyncio

    if not _llm_call_fn or not _extract_json_fn:
        raise ValueError("_llm_call_fn and _extract_json_fn are required")

    # Collect full student text for verification
    student_text = collect_student_text(student_files, vision_notes)

    # Build the checkpoint-based user prompt
    user_prompt = build_checkpoint_grading_prompt(
        checkpoints_by_criterion=checkpoints_by_criterion,
        criteria=criteria,
        student_content=student_text,
        student_files=student_files,
        title=title,
        description=description,
        max_score=max_score,
        vision_notes=vision_notes,
        reference_solution=reference_solution,
    )

    # ─── Step 1: LLM Checkpoint Evaluation ───
    if _rate_limiter:
        await _rate_limiter.acquire()

    # Build messages: system prompt + user content (may include images)
    grading_messages = []
    if messages_with_images:
        # messages_with_images already has image content attached
        # We replace the system and user text but keep image content
        grading_messages = [
            {"role": "system", "content": CHECKPOINT_SYSTEM_PROMPT},
        ]
        # Find the user message and augment it with our prompt
        for msg in messages_with_images:
            if msg.get("role") == "user":
                if isinstance(msg.get("content"), list):
                    # Multi-modal message — prepend our text prompt
                    new_content = [{"type": "text", "text": user_prompt}]
                    for item in msg["content"]:
                        if isinstance(item, dict) and item.get("type") != "text":
                            new_content.append(item)
                    grading_messages.append({"role": "user", "content": new_content})
                else:
                    grading_messages.append({"role": "user", "content": user_prompt})
                break
        else:
            grading_messages.append({"role": "user", "content": user_prompt})
    else:
        grading_messages = [
            {"role": "system", "content": CHECKPOINT_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

    # Determine if we need vision
    has_images = any(
        isinstance(msg.get("content"), list)
        and any(
            isinstance(item, dict) and item.get("type") == "image_url"
            for item in msg["content"]
        )
        for msg in grading_messages
        if msg.get("role") == "user"
    )

    response, call_meta = await asyncio.to_thread(
        _llm_call_fn,
        purpose="checkpoint_grading",
        needs_vision=has_images,
        messages=grading_messages,
        temperature=0.0,
        top_p=1.0,
        max_tokens=4000,
        seed=42,
        preferred_provider=preferred_provider,
    )

    raw_response = (response.choices[0].message.content or "").strip()
    parsed = _extract_json_fn(raw_response)

    if not isinstance(parsed, dict):
        logger.error("Checkpoint grading returned non-dict")
        return _build_error_result(criteria, max_score, "Failed to parse checkpoint response")

    # ─── Step 2: Parse and Verify Results ───
    checkpoint_evals = parsed.get("checkpoint_evaluations", [])
    if not isinstance(checkpoint_evals, list):
        checkpoint_evals = []

    # Parse all evaluations
    eval_by_id: dict[str, dict] = {}
    for ev in checkpoint_evals:
        if not isinstance(ev, dict):
            continue
        cp_id = str(ev.get("checkpoint_id", "")).strip()
        if cp_id:
            eval_by_id[cp_id] = {
                "checkpoint_id": cp_id,
                "pass": bool(ev.get("pass", False)),
                "evidence_quote": str(ev.get("evidence_quote", "") or ""),
                "source_file": str(ev.get("source_file", "") or ""),
                "reasoning": str(ev.get("reasoning", "") or ""),
            }

    # Verify each checkpoint's evidence
    all_flags: list[str] = []
    hallucinated_checkpoints: list[dict] = []
    all_checkpoints_flat: list[dict] = []

    for crit_name, cps in checkpoints_by_criterion.items():
        all_checkpoints_flat.extend(cps)

    for cp in all_checkpoints_flat:
        cp_id = cp["id"]
        result = eval_by_id.get(cp_id)
        if not result:
            # Checkpoint not evaluated — create a fail result
            eval_by_id[cp_id] = {
                "checkpoint_id": cp_id,
                "pass": False,
                "evidence_quote": "",
                "source_file": "",
                "reasoning": "Not evaluated by AI",
                "verified": False,
                "hallucination_detected": False,
                "keyword_inconsistency": None,
            }
            continue

        evidence = result["evidence_quote"]
        verdict = result["pass"]

        # Verification step A: Does evidence exist in submission?
        if verdict and evidence and not evidence.strip().startswith("[VISUAL]"):
            found, similarity, method = verify_evidence_exists(evidence, student_text)
            result["verified"] = found
            result["evidence_similarity"] = similarity
            result["verification_method"] = method

            if not found:
                result["hallucination_detected"] = True
                hallucinated_checkpoints.append(cp)
                logger.warning(
                    "Evidence not found for checkpoint %s (similarity=%.2f): %.80s...",
                    cp_id, similarity, evidence,
                )
            else:
                result["hallucination_detected"] = False
        elif verdict and not evidence:
            # Pass with no evidence — suspicious
            result["verified"] = False
            result["hallucination_detected"] = True
            hallucinated_checkpoints.append(cp)
        elif not verdict:
            # Fail — no evidence needed
            result["verified"] = True
            result["hallucination_detected"] = False
        else:
            # Visual evidence
            result["verified"] = True
            result["hallucination_detected"] = False

        # Verification step B: Keyword check
        kw_ok, kw_reason = verify_checkpoint_keywords(cp, evidence, verdict)
        result["keyword_inconsistency"] = kw_reason if not kw_ok else None
        if not kw_ok and verdict:
            # Keywords missing but checkpoint passes — suspicious
            if result.get("verified"):
                # Evidence exists but keywords don't match — might be wrong checkpoint
                result["verified"] = False
                hallucinated_checkpoints.append(cp)

    # ─── Step 3: Retry Hallucinated Checkpoints ───
    if hallucinated_checkpoints:
        logger.info(
            "Retrying %d hallucinated checkpoints", len(hallucinated_checkpoints)
        )
        retried_results = await _retry_hallucinated_checkpoints(
            hallucinated_checkpoints=hallucinated_checkpoints,
            student_text=student_text,
            eval_by_id=eval_by_id,
            _llm_call_fn=_llm_call_fn,
            _rate_limiter=_rate_limiter,
            _extract_json_fn=_extract_json_fn,
            preferred_provider=preferred_provider,
        )
        # Update results with retried evaluations
        for cp_id, new_result in retried_results.items():
            eval_by_id[cp_id] = new_result

        # After retries: flag any checkpoint that is STILL unverified and passing
        # These were hallucinated and couldn't be fixed — teacher should review
        for cp in hallucinated_checkpoints:
            cp_id = cp["id"]
            final = eval_by_id.get(cp_id, {})
            if final.get("pass") and not final.get("verified"):
                tier = get_evidence_flag_tier(final)
                desc = cp.get("description", cp_id)
                if tier in ("red", "orange"):
                    all_flags.append(
                        f"Unverified evidence after retries for checkpoint: {desc}"
                    )

    # ─── Step 4: Check Cross-Checkpoint Consistency ───
    consistency_issues = check_cross_checkpoint_consistency(
        list(eval_by_id.values()), all_checkpoints_flat
    )
    if consistency_issues:
        for issue in consistency_issues:
            all_flags.append(f"Consistency issue: {issue}")

    # ─── Step 5: Compute Deterministic Scores ───
    rubric_breakdown = []
    total_score = 0.0

    for crit in criteria:
        crit_name = crit.get("criterion", "")
        crit_max = crit.get("max", 0)
        cps = checkpoints_by_criterion.get(crit_name, [])

        if not cps:
            # No checkpoints for this criterion — flag it
            rubric_breakdown.append({
                "criterion": crit_name,
                "score": 0,
                "max": crit_max,
                "justification": "No checkpoints generated for this criterion",
                "checkpoints": [],
                "flagged": True,
                "flag_reasons": ["No checkpoints available — needs manual grading"],
            })
            all_flags.append(f"No checkpoints for '{crit_name}' — needs manual review")
            continue

        # Get results for this criterion's checkpoints
        crit_results = [eval_by_id.get(cp["id"], {}) for cp in cps]

        # Score deterministically
        scoring = score_criterion_from_checkpoints(cps, crit_results)
        score = scoring["score"]
        total_score += score

        # Compute flags for this criterion
        has_visual = any(cp.get("requires_visual") for cp in cps)
        crit_flags = compute_criterion_flags(crit_name, cps, crit_results, has_visual)
        # Only add serious (RED-tier) flags to top-level
        for f in crit_flags:
            if "could not be verified" in f or "not evaluated" in f:
                all_flags.append(f"[{crit_name}] {f}")

        # Build justification from checkpoint evidence
        justification_parts = []
        for cp, result in zip(cps, crit_results):
            is_pass = result.get("pass") and not _is_clear_fabrication(result)
            tier = get_evidence_flag_tier(result)
            status = "✓" if is_pass else "✗"
            tier_tags = {
                "green": "",
                "yellow": " [PARTIAL MATCH]",
                "orange": " [UNCERTAIN]",
                "red": " [FABRICATED]",
                "visual": " [VISUAL]",
                "none": "",
            }
            verified_tag = tier_tags.get(tier, "")
            evidence = result.get("evidence_quote", "")
            reasoning = result.get("reasoning", "")
            desc = cp.get("description", cp["id"])

            if evidence and not evidence.startswith("[VISUAL]"):
                evidence_short = evidence[:150] + ("..." if len(evidence) > 150 else "")
                justification_parts.append(
                    f"{status} {desc}{verified_tag}: \"{evidence_short}\""
                )
            elif evidence:
                justification_parts.append(f"{status} {desc}{verified_tag}: {evidence[:150]}")
            else:
                justification_parts.append(f"{status} {desc}{verified_tag}: No evidence found")

        # Build checkpoint details for the result
        checkpoint_details = []
        for cp, result in zip(cps, crit_results):
            tier = get_evidence_flag_tier(result)
            checkpoint_details.append({
                "id": cp["id"],
                "description": cp.get("description", ""),
                "points": cp.get("points", 0),
                "pass": bool(result.get("pass")),
                "verified": bool(result.get("verified")),
                "evidence_quote": str(result.get("evidence_quote", "")),
                "source_file": str(result.get("source_file", "")),
                "reasoning": str(result.get("reasoning", "")),
                "points_awarded": (
                    cp["points"] if (result.get("pass") and not _is_clear_fabrication(result))
                    else 0
                ),
                "evidence_tier": tier,
                "evidence_similarity": round(result.get("evidence_similarity", 0.0), 3),
                "flagged": bool(
                    tier in ("red", "orange")
                    or result.get("keyword_inconsistency")
                ),
            })

        rubric_breakdown.append({
            "criterion": crit_name,
            "score": round(score, 1),
            "max": crit_max,
            "justification": "\n".join(justification_parts),
            "checkpoints": checkpoint_details,
            "flagged": bool(crit_flags),
            "flag_reasons": crit_flags,
        })

    # Clamp total
    total_score = round(min(total_score, max_score), 1)
    percentage = round((total_score / max_score) * 100, 1) if max_score > 0 else 0

    # Letter grade
    letter_grade = _percentage_to_letter(percentage)

    # Overall confidence based on verification success rate
    total_cps = len(all_checkpoints_flat)
    verified_cps = sum(
        1 for cp in all_checkpoints_flat
        if eval_by_id.get(cp["id"], {}).get("verified", False)
    )
    verification_rate = verified_cps / total_cps if total_cps > 0 else 0

    if verification_rate >= 0.90:
        confidence = "high"
    elif verification_rate >= 0.70:
        confidence = "medium"
    else:
        confidence = "low"

    # Deduplicate flags
    unique_flags = list(dict.fromkeys(all_flags))

    return {
        "total_score": total_score,
        "max_score": max_score,
        "percentage": percentage,
        "letter_grade": letter_grade,
        "rubric_breakdown": rubric_breakdown,
        "overall_feedback": str(parsed.get("overall_feedback", ""))[:2000],
        "strengths": (
            parsed.get("strengths", [])
            if isinstance(parsed.get("strengths"), list)
            else []
        ),
        "weaknesses": (
            parsed.get("weaknesses", [])
            if isinstance(parsed.get("weaknesses"), list)
            else []
        ),
        "suggestions_for_improvement": str(
            parsed.get("suggestions_for_improvement", "")
        )[:1000],
        "confidence": confidence,
        "confidence_reasoning": (
            f"{verified_cps}/{total_cps} checkpoints verified ({verification_rate:.0%} verification rate). "
            + (str(parsed.get("confidence_reasoning", ""))[:200] if parsed.get("confidence_reasoning") else "")
        ).strip(),
        "flags": unique_flags,
        "grading_method": "checkpoint",
        "verification_rate": round(verification_rate, 3),
        "checkpoint_stats": {
            "total": total_cps,
            "verified": verified_cps,
            "hallucinated_and_retried": len(hallucinated_checkpoints),
            "flagged_criteria": sum(1 for c in rubric_breakdown if c.get("flagged")),
        },
        "_call_meta": call_meta,
    }


async def _retry_hallucinated_checkpoints(
    hallucinated_checkpoints: list[dict],
    student_text: str,
    eval_by_id: dict[str, dict],
    _llm_call_fn,
    _rate_limiter,
    _extract_json_fn,
    preferred_provider: str,
    max_retries: int = 2,
) -> dict[str, dict]:
    """Retry evaluation for checkpoints where evidence was not verified.

    Returns updated results for retried checkpoints.
    """
    import asyncio

    updated: dict[str, dict] = {}

    for attempt in range(max_retries):
        # Find checkpoints that still need retrying
        still_failed = [
            cp for cp in hallucinated_checkpoints
            if cp["id"] not in updated or (
                updated[cp["id"]].get("pass")
                and not updated[cp["id"]].get("verified")
            )
        ]

        if not still_failed:
            break

        logger.info("Retry attempt %d for %d checkpoints", attempt + 1, len(still_failed))

        retry_prompt = build_retry_prompt_for_checkpoints(still_failed, student_text)

        try:
            if _rate_limiter:
                await _rate_limiter.acquire()

            response, _ = await asyncio.to_thread(
                _llm_call_fn,
                purpose="checkpoint_retry",
                needs_vision=False,
                messages=[
                    {"role": "system", "content": CHECKPOINT_SYSTEM_PROMPT},
                    {"role": "user", "content": retry_prompt},
                ],
                temperature=0.0,
                top_p=1.0,
                max_tokens=2000,
                seed=42,
                preferred_provider=preferred_provider,
            )

            raw = (response.choices[0].message.content or "").strip()
            parsed = _extract_json_fn(raw)
            if not isinstance(parsed, dict):
                continue

            for ev in parsed.get("checkpoint_evaluations", []):
                if not isinstance(ev, dict):
                    continue
                cp_id = str(ev.get("checkpoint_id", "")).strip()
                if not cp_id:
                    continue

                new_result = {
                    "checkpoint_id": cp_id,
                    "pass": bool(ev.get("pass", False)),
                    "evidence_quote": str(ev.get("evidence_quote", "") or ""),
                    "source_file": str(ev.get("source_file", "") or ""),
                    "reasoning": str(ev.get("reasoning", "") or "") + f" [retry {attempt + 1}]",
                }

                # Re-verify the retried evidence
                evidence = new_result["evidence_quote"]
                verdict = new_result["pass"]

                if verdict and evidence and not evidence.strip().startswith("[VISUAL]"):
                    found, sim, method = verify_evidence_exists(evidence, student_text)
                    new_result["verified"] = found
                    new_result["evidence_similarity"] = sim
                    new_result["hallucination_detected"] = not found
                elif not verdict:
                    new_result["verified"] = True
                    new_result["hallucination_detected"] = False
                else:
                    new_result["verified"] = False
                    new_result["hallucination_detected"] = True

                # Also verify keywords
                cp_data = next((c for c in hallucinated_checkpoints if c["id"] == cp_id), None)
                if cp_data:
                    kw_ok, kw_reason = verify_checkpoint_keywords(cp_data, evidence, verdict)
                    new_result["keyword_inconsistency"] = kw_reason if not kw_ok else None

                updated[cp_id] = new_result

        except Exception as e:
            logger.warning("Checkpoint retry attempt %d failed: %s", attempt + 1, e)
            continue

    # For checkpoints that still failed after all retries, mark as uncertain
    # but KEEP the LLM's original answer if it had evidence — don't override to fail.
    # Scoring logic awards points if evidence exists (even unverified), and only
    # gives 0 for clear fabrication (empty evidence).
    for cp in hallucinated_checkpoints:
        cp_id = cp["id"]
        if cp_id not in updated:
            # No retry response at all — mark as unverified with no evidence
            updated[cp_id] = {
                "checkpoint_id": cp_id,
                "pass": False,
                "evidence_quote": "",
                "source_file": "",
                "reasoning": "Could not verify evidence after retries — no retry response received",
                "verified": False,
                "hallucination_detected": True,
                "keyword_inconsistency": None,
            }
        elif updated[cp_id].get("pass") and not updated[cp_id].get("verified"):
            # LLM says pass with evidence but evidence couldn't be verified.
            # KEEP the LLM's answer — don't override to fail.
            # The scoring function handles this: unverified + has evidence = full points.
            # Only flag it for human review.
            updated[cp_id]["reasoning"] = (
                str(updated[cp_id].get("reasoning", ""))
                + " | Evidence could not be verified after retries — flagged for review"
            )
            updated[cp_id]["hallucination_detected"] = False  # not fabrication, just unverifiable

    return updated


# ═══════════════════════════════════════════════════════════════
# UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def _percentage_to_letter(pct: float) -> str:
    """Convert percentage to letter grade."""
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
    else:
        return "F"


def _build_error_result(
    criteria: list[dict], max_score: int, error_msg: str
) -> dict[str, Any]:
    """Build an error result in the standard format."""
    return {
        "total_score": 0,
        "max_score": max_score,
        "percentage": 0,
        "letter_grade": "F",
        "rubric_breakdown": [
            {
                "criterion": c.get("criterion", ""),
                "score": 0,
                "max": c.get("max", 0),
                "justification": error_msg,
                "checkpoints": [],
                "flagged": True,
                "flag_reasons": [error_msg],
            }
            for c in criteria
        ],
        "overall_feedback": error_msg,
        "strengths": [],
        "weaknesses": [],
        "suggestions_for_improvement": "",
        "confidence": "low",
        "confidence_reasoning": error_msg,
        "flags": [error_msg],
        "grading_method": "checkpoint",
        "error": error_msg,
    }
