"""
Enhanced AI grading engine with:
- Rubric generation from assignment description
- Relevance validation
- Deterministic grading with hashing
- Support for mixed text+vision content
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import threading
from typing import Any, Optional, Dict, List, Tuple

from openai import OpenAI

from app.config import (
    NVIDIA_API_KEY, NVIDIA_BASE_URL, NVIDIA_MODEL, RATE_LIMIT_RPM,
    MAX_IMAGES_FOR_FINAL_GRADE, MAX_IMAGES_SELECTION_POOL,
    MAX_IMAGES_FOR_PREANALYSIS, VISION_PREANALYSIS_CHUNK_SIZE,
    ENABLE_VISION_PREANALYSIS
)

logger = logging.getLogger(__name__)

# SOTA Configuration
MAX_TEXT_CHARS = 50000  # Max text before chunking
MAX_IMAGES_PER_REQUEST = MAX_IMAGES_FOR_FINAL_GRADE  # 8 by default
TEXT_CHUNK_SIZE = 40000  # Smart chunk boundary


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


def _get_client() -> OpenAI:
    return OpenAI(base_url=NVIDIA_BASE_URL, api_key=NVIDIA_API_KEY, timeout=180.0)


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
        # Skip total/max lines and headers
        lower_line = line.lower()
        if lower_line.startswith('total') or lower_line.startswith('max'):
            continue
        if lower_line.startswith('rubric'):
            continue
        
        # Remove common suffixes like "points", "pts", "marks"
        clean_line = re.sub(r'\s*(?:points?|pts?|marks?)\s*$', '', line, flags=re.IGNORECASE)
        
        # Pattern: Match "Problem X: Name - Y" or "Problem X - Name: Y" or "Name: Y"
        # Try to find the points value - usually the LAST number on the line
        numbers = re.findall(r'\b(\d+(?:\.\d+)?)\b', clean_line)
        
        if numbers:
            # Take the LAST number as the points (first numbers are usually problem numbers)
            points = float(numbers[-1])
            
            # Sanity check - points should be reasonable
            if 0 < points < 1000:
                # Remove the points number and separator from the line
                # Find where the points number appears
                points_str = numbers[-1]
                # Remove everything from the last separator before the points
                criterion = re.sub(r'\s*[:\-=]\s*' + re.escape(points_str) + r'.*$', '', clean_line)
                criterion = criterion.strip()
                
                # Clean up trailing punctuation
                criterion = re.sub(r'[:\-=]\s*$', '', criterion).strip()
                
                if criterion and len(criterion) > 1:
                    # Check for duplicates
                    if not any(c['criterion'].lower() == criterion.lower() for c in criteria):
                        criteria.append({"criterion": criterion, "max": points})
    
    return criteria


async def generate_rubric_from_description(
    assignment_description: str,
    max_score: int = 100,
    strictness: str = "balanced"  # "lenient", "balanced", "strict"
) -> dict:
    """
    Generate a rubric from assignment description using AI.
    
    Args:
        assignment_description: The assignment description text
        max_score: Maximum total score
        strictness: How strict the rubric should be ("lenient", "balanced", "strict")
    
    Returns:
        dict with generated rubric and metadata
    """
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
    
    system_prompt = f"""You are an expert Computer Science instructor creating grading rubrics.

{strictness_prompts.get(strictness, strictness_prompts["balanced"])}

Generate a rubric that sums to exactly {max_score} points.

Respond in this exact JSON format:
{{
  "rubric_text": "Criterion 1: XX points\\nCriterion 2: XX points\\n...\\nTotal: {max_score}",
  "criteria": [
    {{"criterion": "Name", "max": XX, "description": "What this criterion assesses"}}
  ],
  "strictness_level": "{strictness}",
  "max_score": {max_score},
  "reasoning": "Brief explanation of why these criteria were chosen"
}}"""

    user_prompt = f"""Assignment Description:
{assignment_description}

Generate a complete grading rubric for this assignment."""

    client = _get_client()
    
    try:
        response = client.chat.completions.create(
            model=NVIDIA_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.3,  # Slight creativity allowed for rubric generation
            max_tokens=1500,
        )
        
        raw_text = response.choices[0].message.content or ""
        result = _extract_json(raw_text)
        
        # Validate the rubric sums correctly
        criteria = result.get("criteria", [])
        total = sum(c.get("max", 0) for c in criteria)
        
        if total != max_score:
            # Adjust proportionally
            factor = max_score / total if total > 0 else 1
            for c in criteria:
                c["max"] = round(c["max"] * factor)
            # Fix rounding errors on last item
            current_total = sum(c["max"] for c in criteria)
            if current_total != max_score and criteria:
                criteria[-1]["max"] += max_score - current_total
        
        return {
            "success": True,
            "rubric_text": result.get("rubric_text", ""),
            "criteria": criteria,
            "strictness": strictness,
            "max_score": max_score,
            "reasoning": result.get("reasoning", "")
        }
        
    except Exception as e:
        logger.exception("Failed to generate rubric")
        return {
            "success": False,
            "error": str(e),
            "rubric_text": "",
            "criteria": []
        }


async def validate_submission_relevance(
    title: str,
    description: str,
    student_files: List[ExtractedContent],
    rubric: str
) -> dict:
    """
    Validate that a submission is relevant to the assignment.
    
    Returns:
        dict with is_relevant, confidence, flags, and reasoning
    """
    await _rate_limiter.acquire()
    
    # Quick content check
    total_text = ""
    for content in student_files:
        if content.text_content:
            total_text += content.text_content + "\n"
    
    # Check for empty or near-empty submissions
    if len(total_text.strip()) < 50 and not any(c.images for c in student_files):
        return {
            "is_relevant": False,
            "confidence": "high",
            "flags": ["empty_submission"],
            "reasoning": "Submission contains minimal or no content"
        }
    
    # Check for obviously wrong file types
    file_types = [c.file_type for c in student_files]
    has_code = "code" in file_types
    has_text = "text" in file_types or "docx" in file_types or "pdf" in file_types
    
    # Build prompt for AI validation
    system_prompt = """You are checking if a student submission is relevant to the given assignment.

Respond in this exact JSON format:
{
  "is_relevant": true/false,
  "confidence": "high/medium/low",
  "flags": ["list", "of", "issues"],
  "reasoning": "Explanation of why it's relevant or not"
}

Possible flags:
- "empty_submission": Little to no content
- "wrong_assignment": Clearly about a different topic
- "incomplete": Major sections missing
- "template_only": Only contains template/boilerplate code
- "placeholder_content": Contains "TODO", "FIXME", or placeholder text
- "off_topic": Content doesn't match assignment requirements"""

    user_prompt = f"""Assignment: {title}
Description: {description}
Rubric: {rubric}

Student Submission Content (first 3000 chars):
{total_text[:3000]}

Is this submission relevant to the assignment?"""

    client = _get_client()
    
    try:
        response = client.chat.completions.create(
            model=NVIDIA_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.0,
            max_tokens=500,
        )
        
        raw_text = response.choices[0].message.content or ""
        result = _extract_json(raw_text)
        
        return {
            "is_relevant": result.get("is_relevant", True),
            "confidence": result.get("confidence", "medium"),
            "flags": result.get("flags", []),
            "reasoning": result.get("reasoning", "")
        }
        
    except Exception as e:
        logger.exception("Relevance validation failed")
        # Default to relevant if check fails
        return {
            "is_relevant": True,
            "confidence": "low",
            "flags": ["validation_error"],
            "reasoning": f"Validation failed: {str(e)}. Defaulting to relevant."
        }


SYSTEM_PROMPT = """You are a FAIR and CONSISTENT academic grader.

CRITICAL RULES:
1. Grade ONLY what is submitted - do not assume anything
2. Your rubric_breakdown MUST match the provided rubric EXACTLY
3. Each criterion name in your response MUST match the rubric criterion name exactly
4. The "max" value for each criterion MUST match the rubric exactly
5. Sum of all rubric scores = total_score
6. total_score MUST NOT exceed the max_score
7. Provide SPECIFIC evidence for every score

RESPONSE FORMAT - Return ONLY valid JSON:
{
  "rubric_breakdown": [
    {"criterion": "<EXACT name from rubric>", "score": <number>, "max": <exact max from rubric>, "justification": "<specific evidence>"}
  ],
  "total_score": <sum of rubric scores>,
  "overall_feedback": "<summary>",
  "strengths": ["<specific strength>"],
  "weaknesses": ["<specific weakness>"],
  "suggestions_for_improvement": "<advice>",
  "confidence": "<high|medium|low>"
}

IMPORTANT: Do NOT add extra rubric criteria. Use ONLY the criteria provided in the rubric."""


# ============================================================================
# SOTA FEATURES: Smart Chunking, Multi-turn Grading, Batch Processing
# ============================================================================

def _get_file_language(filename: str, file_type: str) -> str:
    """Detect programming language from filename and type."""
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    lang_map = {
        'py': 'python', 'java': 'java', 'cpp': 'cpp', 'c': 'c', 'h': 'c',
        'js': 'javascript', 'ts': 'typescript', 'jsx': 'jsx', 'tsx': 'tsx',
        'cs': 'csharp', 'go': 'go', 'rb': 'ruby', 'php': 'php',
        'swift': 'swift', 'kt': 'kotlin', 'scala': 'scala', 'rs': 'rust',
        'sh': 'bash', 'sql': 'sql', 'html': 'html', 'css': 'css',
    }
    if ext in lang_map:
        return lang_map[ext]
    if file_type == 'code':
        return 'unknown'
    return ''


def _smart_extract_text_with_chunking(student_files: list, max_chars: int = 50000) -> Tuple[str, dict]:
    """SOTA: Extract text with intelligent chunking that respects code boundaries."""
    from app.services.file_parser_enhanced import _smart_chunk_code

    text_parts = []
    total_chars = 0
    chunk_info = []

    for f in student_files:
        if hasattr(f, 'text_content'):
            file_type = f.file_type
            filename = f.filename
            content = f.text_content
        else:
            file_type = f.get("type", "")
            filename = f.get("filename", "unknown")
            content = f.get("content")

        if file_type in ("image", "pdf_images", "error", "missing", "binary", "archive"):
            continue
        if content is None or not isinstance(content, str):
            continue
        if not content.strip():
            continue

        if len(content) > TEXT_CHUNK_SIZE:
            lang = _get_file_language(filename, file_type)
            chunked_content, chunk_meta = _smart_chunk_code(content, lang, TEXT_CHUNK_SIZE)

            header = f"\n=== {filename} ({file_type}) [CHUNKED: {chunk_meta['total_chunks']} parts] ===\n"
            text_parts.append(header)
            text_parts.append(chunked_content)
            chunk_info.append({
                'filename': filename,
                'chunks': chunk_meta['total_chunks'],
                'truncated': chunk_meta['truncated'],
                'missing_code_units': chunk_meta.get('missing_code_units', [])
            })
            total_chars += len(header) + len(chunked_content)
        else:
            header = f"\n=== {filename} ({file_type}) ===\n"
            remaining = max_chars - total_chars - len(header)
            if remaining <= 0:
                text_parts.append(f"\n[... MORE FILES TRUNCATED ...]")
                break

            file_content = content[:remaining]
            text_parts.append(header)
            text_parts.append(file_content)
            total_chars += len(header) + len(file_content)

    if total_chars > max_chars:
        text_parts.append(f"\n[TOTAL CONTENT TRUNCATED from {total_chars} to {max_chars} chars]")

    result_text = "\n".join(text_parts)

    return result_text, {
        'total_chars': total_chars,
        'was_truncated': total_chars > max_chars,
        'chunk_info': chunk_info
    }


def _rank_and_select_images(student_files: list, max_images: int = 8) -> Tuple[List[dict], dict]:
    """SOTA: Rank and select most relevant images using relevance scoring."""
    all_images = []

    for f in student_files:
        filename = getattr(f, 'filename', None) or f.get('filename', 'unknown')

        if hasattr(f, 'images') and f.images:
            for idx, img in enumerate(f.images):
                page_num = img.get('page')
                desc = (img.get('description') or '').lower()

                score = 0
                if page_num is not None:
                    score += 10
                    score += max(0, 10 - page_num)
                if 'code' in desc or 'solution' in desc:
                    score += 5
                if 'diagram' in desc or 'flow' in desc or 'chart' in desc:
                    score += 3

                all_images.append({
                    'source_file': filename,
                    'base64': img.get('base64'),
                    'media_type': img.get('media_type', 'image/png'),
                    'page': page_num,
                    'description': img.get('description'),
                    'score': score,
                    'index': idx
                })

    all_images.sort(key=lambda x: x['score'], reverse=True)
    selected = all_images[:max_images]
    total_available = len(all_images)

    return selected, {
        'total_available': total_available,
        'selected_count': len(selected),
        'was_truncated': total_available > max_images,
        'selection_method': 'relevance_ranked',
        'top_scores': [img['score'] for img in selected[:3]]
    }


# ============================================================================
# MULTI-TURN GRADING WITH PRE-ANALYSIS
# ============================================================================

IMAGE_PREANALYSIS_PROMPT = """You are an expert at analyzing student submissions.
Your task is to analyze ALL the images provided and identify which contain IMPORTANT GRADING INFORMATION.

For each image, classify it as:
- "IMPORTANT_CODE": Contains code, algorithms, or solutions
- "IMPORTANT_DIAGRAM": Contains flowcharts, architecture, diagrams, graphs
- "IMPORTANT_WRITTEN": Contains written explanations, answers, analysis
- "MINOR": Front page, cover, title, decorative elements, or irrelevant content

Provide your response as a JSON object:
{
  "analyzed_images": [
    {
      "index": 0,
      "source_file": "filename",
      "classification": "IMPORTANT_CODE|IMPORTANT_DIAGRAM|IMPORTANT_WRITTEN|MINOR",
      "relevance_score": 1-10,
      "summary": "Brief description of what's in this image"
    }
  ],
  "total_important": <number>,
  "recommendation": "Which images should definitely be included in grading?"
}

Be thorough - analyze EVERY image carefully."""


TEXT_PREANALYSIS_PROMPT = """You are an expert at analyzing code submissions.
Analyze the following code and provide a STRUCTURED SUMMARY that captures:
1. Overall file structure and purpose
2. Key functions/methods and what they do
3. Classes and their responsibilities
4. Any algorithms or data structures used
5. Potential issues or bugs

Provide response as JSON:
{
  "file_summary": {
    "filename": "main.py",
    "purpose": "What this file does",
    "key_functions": ["function1: does X", "function2: does Y"],
    "classes": ["Class1: brief description"],
    "algorithms": ["BFS", "A* search", etc],
    "potential_issues": ["issue1", "issue2"]
  },
  "overall_structure": "Brief summary of how files work together",
  "completed_requirements": ["req1", "req2"],
  "missing_requirements": ["req3"]
}

This summary will be used for grading, so be thorough and accurate."""


async def _preanalyze_images(
    client,
    student_files: list,
    max_images_for_preanalysis: int = 20,
) -> dict:
    """
    MULTI-TURN SOTA: Pre-analyze ALL images to identify which are most relevant.
    This uses actual AI analysis, not just page numbers.
    """
    # First, collect all available images (up to limit)
    all_images = []
    for f in student_files:
        filename = getattr(f, 'filename', None) or f.get('filename', 'unknown')
        if hasattr(f, 'images') and f.images:
            for idx, img in enumerate(f.images[:max_images_for_preanalysis]):
                all_images.append({
                    'source_file': filename,
                    'base64': img.get('base64'),
                    'media_type': img.get('media_type', 'image/png'),
                    'page': img.get('page'),
                    'index': len(all_images)
                })

    if not all_images:
        return {'selected': [], 'summary': 'No images to analyze'}

    # Build content with all images
    content = [{"type": "text", "text": IMAGE_PREANALYSIS_PROMPT}]
    for img in all_images:
        if img['base64']:
            desc = f"Image {img['index']}"
            if img['page']:
                desc += f" (Page {img['page']})"
            desc += f" from {img['source_file']}"
            content.append({"type": "text", "text": f"\n[{desc}]"})
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{img['media_type']};base64,{img['base64']}",
                    "detail": "low"  # Use low detail for pre-analysis (faster)
                }
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
            max_tokens=2000,
            seed=42,
        )

        raw_response = response.choices[0].message.content or ""

        # Try to parse JSON from response
        try:
            import json
            # Find JSON in response
            json_start = raw_response.find('{')
            json_end = raw_response.rfind('}') + 1
            if json_start >= 0 and json_end > json_start:
                analysis = json.loads(raw_response[json_start:json_end])
            else:
                analysis = {'error': 'Could not parse analysis'}

            # Extract important images based on AI analysis
            selected_images = []
            if 'analyzed_images' in analysis:
                # Sort by relevance score
                sorted_images = sorted(
                    analysis['analyzed_images'],
                    key=lambda x: x.get('relevance_score', 0),
                    reverse=True
                )
                # Select top scored as IMPORTANT
                for img_analysis in sorted_images[:8]:  # Take top 8
                    if img_analysis.get('classification', 'MINOR') != 'MINOR':
                        idx = img_analysis.get('index', 0)
                        if idx < len(all_images):
                            selected_images.append({
                                **all_images[idx],
                                'classification': img_analysis.get('classification'),
                                'ai_summary': img_analysis.get('summary', ''),
                                'relevance_score': img_analysis.get('relevance_score', 0)
                            })

            # If AI analysis failed, fall back to page-based selection
            if not selected_images:
                selected_images = all_images[:8]

            return {
                'selected': selected_images[:8],
                'analysis': analysis,
                'total_analyzed': len(all_images),
                'method': 'ai_preanalysis'
            }

        except Exception as e:
            logger.warning(f"Failed to parse image analysis: {e}")
            # Fallback to simple selection
            return {
                'selected': all_images[:8],
                'analysis': {'error': str(e)},
                'total_analyzed': len(all_images),
                'method': 'fallback_page_based'
            }

    except Exception as e:
        logger.error(f"Image pre-analysis failed: {e}")
        return {
            'selected': all_images[:8] if all_images else [],
            'error': str(e),
            'method': 'error_fallback'
        }


async def _preanalyze_text(
    client,
    student_files: list,
    max_chars: int = 30000,
) -> dict:
    """
    MULTI-TURN SOTA: Pre-analyze large text content to extract structure.
    """
    # Collect text content (only code files)
    text_parts = []
    file_summaries = []

    for f in student_files:
        if hasattr(f, 'text_content'):
            file_type = f.file_type
            filename = f.filename
            content = f.text_content
        else:
            file_type = f.get("type", "")
            filename = f.get("filename", "unknown")
            content = f.get("content")

        if file_type in ("image", "pdf_images", "error", "missing", "binary", "archive"):
            continue
        if content is None or not isinstance(content, str):
            continue

        text_parts.append(f"=== FILE: {filename} ===\n{content[:8000]}")  # Limit per file

    if not text_parts:
        return {'summary': 'No text to analyze', 'structure': {}}

    full_text = TEXT_PREANALYSIS_PROMPT + "\n\n" + "\n\n".join(text_parts[:10])  # Max 10 files

    messages = [
        {"role": "system", "content": "You are an expert code analyst."},
        {"role": "user", "content": full_text}
    ]

    try:
        await _rate_limiter.acquire()
        response = client.chat.completions.create(
            model=NVIDIA_MODEL,
            messages=messages,
            temperature=0.0,
            max_tokens=2500,
            seed=42,
        )

        raw_response = response.choices[0].message.content or ""

        # Parse JSON
        try:
            import json
            json_start = raw_response.find('{')
            json_end = raw_response.rfind('}') + 1
            if json_start >= 0 and json_end > json_start:
                structure = json.loads(raw_response[json_start:json_end])
            else:
                structure = {'raw_summary': raw_response[:500]}

            return {
                'structure': structure,
                'method': 'ai_preanalysis'
            }
        except Exception as e:
            return {
                'structure': {'raw_text_preview': full_text[:1000]},
                'error': str(e),
                'method': 'fallback'
            }

    except Exception as e:
        logger.error(f"Text pre-analysis failed: {e}")
        return {'error': str(e), 'method': 'error_fallback'}


async def _grade_with_multiturn_context(
    client=None,
    title: str = "",
    description: str = "",
    rubric: str = "",
    max_score: int = 100,
    student_files: list = None,
    questions: Optional[list[dict]] = None,
) -> dict:
    """
    MULTI-TURN SOTA: True multi-pass grading with pre-analysis.

    Pass 1: Pre-analyze images to find the most relevant ones
    Pass 2: (Optional) Pre-analyze text structure for large submissions
    Pass 3: Final grading with full context from pre-analysis

    Can be called with just client (for ai_grader_fixed) or with all params (standalone).
    """
    if student_files is None:
        student_files = []

    # Use passed client, or get our own using local function
    grading_client = client  # Client must be passed from caller
    rubric_criteria = parse_rubric(rubric)

    # === PASS 1: Pre-analyze images ===
    image_analysis = await _preanalyze_images(grading_client, student_files)
    selected_images = image_analysis.get('selected', [])

    # === PASS 2: Pre-analyze text (only for large submissions) ===
    text_analysis = None
    total_text_chars = sum(
        len(getattr(f, 'text_content', '') or f.get('content', '') or '')
        for f in student_files
        if hasattr(f, 'text_content') or f.get('type') not in ('image', 'pdf_images')
    )

    if total_text_chars > 30000:  # Only for large submissions
        text_analysis = await _preanalyze_text(grading_client, student_files)

    # === PASS 3: Final grading with context ===
    # Build user prompt
    user_text = _build_user_prompt(title, description, rubric, max_score, student_files, questions)

    # Extract text content
    text_content, text_meta = _smart_extract_text_with_chunking(student_files, MAX_TEXT_CHARS)

    # Add pre-analysis context to the prompt
    context_note = "\n\n=== PRE-ANALYSIS CONTEXT ===\n"
    if image_analysis.get('method') == 'ai_preanalysis':
        context_note += f"Image Analysis: Analyzed {image_analysis.get('total_analyzed', 0)} images\n"
        if image_analysis.get('analysis', {}).get('recommendation'):
            context_note += f"Recommendation: {image_analysis['analysis']['recommendation']}\n"

    if text_analysis and text_analysis.get('method') == 'ai_preanalysis':
        structure = text_analysis.get('structure', {})
        if 'overall_structure' in structure:
            context_note += f"\nCode Structure Summary: {structure['overall_structure'][:500]}\n"

    full_text = user_text + context_note + "\n\nFILE CONTENTS:\n" + text_content

    # Build content with selected images
    content: list[dict] = [{"type": "text", "text": full_text}]

    for img in selected_images:
        if img.get('base64'):
            desc = f"{img.get('source_file', 'Image')}"
            if img.get('classification'):
                desc += f" [{img['classification']}]"
            if img.get('ai_summary'):
                desc += f": {img['ai_summary']}"

            content.append({"type": "text", "text": f"\n[{desc}]"})
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{img.get('media_type', 'image/png')};base64,{img['base64']}",
                    "detail": "auto"
                }
            })

    # Add note if we didn't analyze all images
    if image_analysis.get('total_analyzed', 0) > len(selected_images):
        content[0]["text"] += f"\n\n[NOTE: {image_analysis['total_analyzed'] - len(selected_images)} additional images were available but not shown]"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content}
    ]

    try:
        await _rate_limiter.acquire()
        response = grading_client.chat.completions.create(
            model=NVIDIA_MODEL,
            messages=messages,
            temperature=0.0,
            max_tokens=4000,
            seed=42,
        )

        raw_text = response.choices[0].message.content or ""
        result = _extract_json(raw_text)
        validated = _validate_result(result, rubric_criteria, max_score)

        # Add comprehensive SOTA metadata
        validated["sota_metadata"] = {
            "grading_strategy": "multi_turn_with_preanalysis",
            "passes": {
                "image_preanalysis": image_analysis.get('method', 'none'),
                "text_preanalysis": text_analysis.get('method', 'none') if text_analysis else 'skipped',
                "final_grading": "completed"
            },
            "image_analysis": {
                "method": image_analysis.get('method'),
                "total_analyzed": image_analysis.get('total_analyzed', 0),
                "selected_count": len(selected_images),
                "selected_images": [
                    {
                        "source": img.get('source_file'),
                        "classification": img.get('classification'),
                        "relevance_score": img.get('relevance_score')
                    }
                    for img in selected_images[:4]  # Include top 4 details
                ]
            },
            "text_analysis": text_analysis,
            "text_processing": text_meta,
            "context_preserved": True,
            "multi_turn": True,
        }

        validated["images_processed"] = len(selected_images)
        validated["text_chars_processed"] = text_meta['total_chars']

        return validated

    except Exception as e:
        logger.error(f"Multi-turn grading failed: {e}")
        raise


async def _grade_with_sota_context(
    client,
    title: str,
    description: str,
    rubric: str,
    max_score: int,
    student_files: list,
    questions: Optional[list[dict]] = None,
) -> dict:
    """SOTA: Multi-turn grading with AI-powered pre-analysis."""
    # Use the multi-turn approach with pre-analysis
    return await _grade_with_multiturn_context(
        client, title, description, rubric, max_score, student_files, questions
    )


def _build_user_prompt(
    title: str,
    description: str,
    rubric: str,
    max_score: int,
    student_files: list,
    questions: Optional[list[dict]] = None,
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
            # New ExtractedContent format
            fn = f.filename
            ft = f.file_type
            if f.images:
                parts.append(f"  {i}. {fn} ({ft} - {len(f.images)} images, {len(f.text_content or '')} text chars)")
            else:
                parts.append(f"  {i}. {fn} ({ft} - {len(f.text_content or '')} chars)")
        else:
            # Old format
            ft = f.get("type", "unknown")
            fn = f.get("filename", "unknown")
            if ft == "pdf_images":
                parts.append(f"  {i}. {fn} (PDF - {f.get('page_count', '?')} pages as images)")
            elif ft == "image":
                parts.append(f"  {i}. {fn} (Image)")
            elif ft in ("code", "text", "notebook", "docx"):
                parts.append(f"  {i}. {fn} ({ft} - content provided below)")
            elif ft == "error":
                parts.append(f"  {i}. {fn} (ERROR: {f.get('error', 'unknown')})")
            elif ft == "missing":
                parts.append(f"  {i}. {fn} (FILE NOT FOUND)")
            else:
                parts.append(f"  {i}. {fn} ({ft})")
    
    return "\n".join(parts)


def _extract_text_content(student_files: list, max_chars: int = 30000) -> str:
    """Extract text content from code/text files."""
    text_parts = []
    total_chars = 0
    
    for f in student_files:
        if total_chars >= max_chars:
            text_parts.append("\n[TRUNCATED]")
            break
            
        # Handle both new and old formats
        if hasattr(f, 'text_content'):
            file_type = f.file_type
            filename = f.filename
            content = f.text_content
        else:
            file_type = f.get("type", "")
            filename = f.get("filename", "unknown")
            content = f.get("content")
        
        if file_type in ("image", "pdf_images", "error", "missing", "binary", "archive"):
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


def _build_multimodal_content(
    user_text: str,
    text_content: str,
    student_files: list,
    max_images: int = 20,
) -> tuple[list[dict], int]:
    """Build content with text and images."""
    
    full_text = user_text + "\n\nFILE CONTENTS:\n" + text_content
    
    content: list[dict] = [{"type": "text", "text": full_text}]
    image_count = 0

    for f in student_files:
        if image_count >= max_images:
            break

        # Handle new ExtractedContent format
        if hasattr(f, 'images'):
            for img in f.images:
                if image_count >= max_images:
                    break
                desc = img.get('description')
                if not desc:
                    page_num = img.get('page')
                    desc = f"Page {page_num}" if page_num else "Embedded image"
                content.append({
                    "type": "text",
                    "text": f"\n[Image from {f.filename}: {desc}]"
                })
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{img.get('media_type', 'image/png')};base64,{img['base64']}",
                        "detail": "auto"
                    },
                })
                image_count += 1
        else:
            # Old format
            file_type = f.get("type")
            
            if file_type == "image" and f.get("content"):
                content.append({
                    "type": "text",
                    "text": f"\n[Image: {f.get('filename', 'unknown')}]"
                })
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{f.get('media_type', 'image/png')};base64,{f['content']}",
                        "detail": "auto"
                    },
                })
                image_count += 1

            elif file_type == "pdf_images" and isinstance(f.get("content"), list):
                for i, img_b64 in enumerate(f["content"][:max_images - image_count]):
                    content.append({
                        "type": "text",
                        "text": f"\n[PDF Page {i + 1}]"
                    })
                    content.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{img_b64}",
                            "detail": "auto"
                        },
                    })
                    image_count += 1

    return content, image_count


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
            
            fixed_breakdown.append({
                "criterion": matched_data['criterion'],
                "score": round(score, 1),
                "max": matched_data['max'],
                "justification": str(item.get("justification", item.get("feedback", "")))[:500]
            })
            used_keys.add(matched_key)
    
    for rubric_key, rubric_data in rubric_map.items():
        if rubric_key not in used_keys:
            fixed_breakdown.append({
                "criterion": rubric_data['criterion'],
                "score": 0,
                "max": rubric_data['max'],
                "justification": "Not assessed"
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
    """
    Compute a hash of the inputs to verify grading consistency.
    Same inputs should always produce the same hash.
    """
    # Normalize the inputs
    content_parts = []
    
    for f in student_files:
        if hasattr(f, 'text_content'):
            content_parts.append(f.text_content or "")
        else:
            content_parts.append(f.get("content", "") or "")
    
    # Combine with rubric and max_score
    hash_input = json.dumps({
        "contents": content_parts,
        "rubric": rubric,
        "max_score": max_score
    }, sort_keys=True)
    
    return hashlib.sha256(hash_input.encode()).hexdigest()[:16]


async def grade_student(
    title: str,
    description: str,
    rubric: str,
    max_score: int,
    student_files: list[dict],
    questions: Optional[list[dict]] = None,
    skip_validation: bool = False,
) -> dict[str, Any]:
    """Grade a student submission."""
    
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
        }
    
    # Compute grading hash for consistency verification
    grading_hash = compute_grading_hash(student_files, rubric, max_score)
    
    # Use SOTA approach: smart text chunking + image ranking
    try:
        client = _get_client()
        result = await _grade_with_sota_context(
            client, title, description, rubric, max_score, student_files, questions
        )
        result["grading_hash"] = grading_hash
        logger.info(f"Graded (SOTA): {result['total_score']}/{max_score} ({result['letter_grade']})")
        return result
    except Exception as e:
        logger.warning(f"SOTA approach failed: {e}, using standard approach")

    # Standard approach (fallback)
    user_text = _build_user_prompt(title, description, rubric, max_score, student_files, questions)
    text_content = _extract_text_content(student_files)
    user_content, img_count = _build_multimodal_content(user_text, text_content, student_files)
    
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content}
    ]
    
    logger.info(f"Grading {len(student_files)} files, {img_count} images, {len(text_content)} text chars")
    
    raw_text = ""
    for attempt in range(3):
        try:
            response = grading_client.chat.completions.create(
                model=NVIDIA_MODEL,
                messages=messages,
                temperature=0.0,  # Deterministic
                max_tokens=3000,
                seed=42,  # For reproducibility
            )
            raw_text = response.choices[0].message.content or ""
            
            result = _extract_json(raw_text)
            validated = _validate_result(result, rubric_criteria, max_score)
            
            # Add metadata
            validated["grading_hash"] = grading_hash
            validated["images_processed"] = img_count
            validated["text_chars_processed"] = len(text_content)
            
            logger.info(f"Graded: {validated['total_score']}/{max_score} ({validated['letter_grade']}) hash={grading_hash}")
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
    }


# Import ExtractedContent for type hints
from app.services.file_parser_enhanced import ExtractedContent
