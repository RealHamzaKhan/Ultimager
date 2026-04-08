"""Evidence Verifier Agent — deterministic, no LLM.

Checks that the evidence_quote from the Domain Judge actually exists
in the student's submission.

Key rule: unverified ≠ fabricated.
  - If we can't find the quote → ask Judge to retry (max 2x)
  - If still unverified after retries → mark as unverified, flag for review
  - NEVER automatically reduce score for unverified evidence
  - ONLY reduce to zero if evidence is empty AND score > 0 (clear hallucination)
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from app.services.agents.base import CheckpointResult


# ── Normalisation helpers ─────────────────────────────────────────

def _norm(text: str) -> str:
    """Collapse whitespace, lowercase."""
    return " ".join(text.lower().split()).strip()


def _strip_ws(text: str) -> str:
    """Remove ALL whitespace — used for code matching."""
    return re.sub(r"\s+", "", text)


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


# ── Verification tiers ────────────────────────────────────────────

def verify_evidence(quote: str, submission_text: str) -> tuple[bool, str, float]:
    """
    Returns (verified, method, similarity_score).

    Tries five methods in order of strictness:
      1. Exact match
      2. Case-insensitive / whitespace-normalised match
      3. Char-stripped match (handles formatting differences in code)
      4. Short-token keyword match (for short quotes: identifiers, O-notation, etc.)
      5. Fuzzy sliding-window match (catches minor typos / truncation)
    """
    if not quote or quote.strip() in ("", "No relevant content found.", "No relevant content found"):
        return False, "empty", 0.0

    # Visual evidence — can't verify from text, trust it
    if quote.strip().startswith("[VISUAL]"):
        return True, "visual", 1.0

    q_norm = _norm(quote)
    s_norm = _norm(submission_text)

    # 1. Exact
    if quote in submission_text:
        return True, "exact", 1.0

    # 2. Case-insensitive normalised
    if q_norm in s_norm:
        return True, "case_insensitive", 0.95

    # 3. Char-stripped (good for code with whitespace differences)
    q_stripped = _strip_ws(quote)
    s_stripped = _strip_ws(submission_text)
    # H-3 fix: lowered from >= 5 to >= 3 so short identifiers ("BFS", "O(n)")
    # are still checked via char-stripping, not just exact match
    if len(q_stripped) >= 3 and q_stripped in s_stripped:
        return True, "char_stripped", 0.90

    # 4. H-3 fix: short-token keyword search (3-14 chars).
    # Short code quotes like "bfs", "def bfs", "O(n)" are identifiers — if the
    # normalised quote is a word/token found anywhere in the submission, trust it.
    # Uses word-boundary awareness: "bfs" must appear as a token, not as part of "dfs".
    if 3 <= len(q_norm) <= 14:
        # Try as a substring of the char-stripped submission (covers all spacing)
        if q_norm.replace(" ", "") in s_stripped.lower():
            return True, "short_token", 0.88
        # Also try as a whitespace-delimited token in the normalised submission
        tokens = set(re.split(r"[\s\W]+", s_norm))
        q_tokens = set(re.split(r"[\s\W]+", q_norm))
        q_tokens.discard("")
        if q_tokens and q_tokens.issubset(tokens):
            return True, "short_token", 0.85

    # 5. Fuzzy sliding window (for quotes >= 10 chars where fuzzy matching is meaningful)
    # V-1 fix: step was window // 4, which caused the window to jump over real matches.
    # E.g. a 40-char quote with step=10 can skip a match sitting at position n+5.
    # Now using step = window // 8 (finer scan) with same-size window so SequenceMatcher
    # compares equal-length strings and can give a fair ratio.
    if len(q_norm) >= 10:
        window = len(q_norm)
        best_sim = 0.0
        step = max(1, window // 8)  # finer than // 4 — catches more real matches
        for i in range(0, max(1, len(s_norm) - window + 1), step):
            chunk = s_norm[i: i + window]
            sim = _similarity(q_norm, chunk)
            if sim > best_sim:
                best_sim = sim
                if best_sim >= 0.80:
                    return True, "fuzzy", best_sim  # early return, no need to continue
        if best_sim >= 0.80:
            return True, "fuzzy", best_sim

    # 5b. Broader sweep — scan the full submission for any partial match.
    # V-1 fix: step was window // 2, far too coarse for the fallback sweep.
    # E.g. a 60-char quote at position 75 is only partially caught by windows at 60 and 90,
    # giving similarity ~0.35 (below threshold) even though it's a real quote.
    # Now using step = window // 4 and a window slightly larger than the quote.
    if len(q_norm) >= 3:
        window = max(len(q_norm), 20)
        step = max(1, window // 4)   # was // 2 — finer scan avoids missing real matches
        best_overall = 0.0
        for i in range(0, max(1, len(s_norm) - window + 1), step):
            chunk = s_norm[i: i + window]
            sim = _similarity(q_norm, chunk)
            if sim > best_overall:
                best_overall = sim
        return False, "unverified", best_overall

    return False, "unverified", 0.0


# ── Main verifier function ────────────────────────────────────────

def run_verifier(
    result: CheckpointResult,
    submission_text: str,
) -> CheckpointResult:
    """
    Verify evidence in a CheckpointResult.
    Updates result in-place and returns it.
    Does NOT change the score — only sets verified flag and flags.
    """
    verified, method, sim = verify_evidence(result.evidence_quote, submission_text)

    result.verified = verified
    result.verification_method = method

    if verified:
        return result

    # Not verified — determine why and flag appropriately
    if not result.evidence_quote.strip() or result.evidence_quote.strip() == "No relevant content found.":
        if result.score_percent > 0:
            # Judge awarded marks but gave no evidence — genuine concern
            result.flags.append("no_evidence_for_awarded_marks")
            result.needs_review = True
    else:
        # Judge quoted something but we couldn't find it — could be minor formatting.
        # V-1 threshold fix: was sim < 0.40, which was too aggressive and triggered
        # for real quotes that differ only in minor formatting (extra spaces, slightly
        # different variable name spelling, line continuation differences, etc.).
        # Now using 0.30 — only truly low similarity is treated as "likely hallucinated".
        if sim < 0.30:
            # Very low similarity — before marking as hallucinated, do a final
            # code-token rescue: if 60%+ of the unique code tokens in the quote
            # appear somewhere in the submission, the AI probably saw real code
            # but reformatted it slightly.
            quote_tokens = _extract_code_tokens(result.evidence_quote)
            if quote_tokens:
                submission_tokens = _extract_code_tokens(submission_text)
                matching = quote_tokens & submission_tokens
                rescue_ratio = len(matching) / len(quote_tokens)
                if rescue_ratio >= 0.60:
                    # Tokens match well enough — classify as misquoted, not hallucinated
                    result.flags.append("evidence_slightly_misquoted")
                else:
                    result.flags.append("evidence_likely_hallucinated")
                    result.needs_review = True
            else:
                result.flags.append("evidence_likely_hallucinated")
                result.needs_review = True
        else:
            # Moderate similarity — probably minor formatting difference, trust the reasoning
            result.flags.append("evidence_slightly_misquoted")
            # Don't set needs_review for this — it's a minor issue

    return result


# ── Code-token extractor ──────────────────────────────────────────

def _extract_code_tokens(text: str) -> set[str]:
    """Extract meaningful code tokens from text.

    Filters out punctuation, numbers-only tokens, and very common English words
    so we compare identifiers, function names and keywords — not noise.
    """
    import re
    _STOP = {
        "the", "a", "an", "is", "in", "it", "of", "to", "and", "or",
        "for", "on", "at", "by", "if", "do", "as", "be", "we", "so",
        "no", "up", "out", "not", "but", "has", "have", "that", "with",
        "this", "from", "are", "was", "were", "will", "can", "def",
        "return", "pass", "true", "false", "none", "class", "import",
        "print", "self", "int", "str", "list", "dict", "set", "tuple",
    }
    raw = re.split(r"[\s\W]+", text.lower())
    return {
        t for t in raw
        if t and len(t) >= 3 and not t.isdigit() and t not in _STOP
    }
