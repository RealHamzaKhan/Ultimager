"""AI grading engine — constructs prompts, calls NVIDIA NIM, parses results.

Includes a token-bucket rate limiter to stay within 40 req/min.
Supports multimodal inputs (vision) for grading images, PDFs, diagrams, and handwritten work.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import threading
from typing import Any, Optional

from openai import OpenAI

from app.config import NVIDIA_API_KEY, NVIDIA_BASE_URL, NVIDIA_MODEL, RATE_LIMIT_RPM

logger = logging.getLogger(__name__)

# ── Rate Limiter ──────────────────────────────────────────────────

class RateLimiter:
    """Sliding-window rate limiter (thread-safe)."""

    def __init__(self, max_requests: int = RATE_LIMIT_RPM, per_seconds: int = 60):
        self.max_requests = max_requests
        self.per_seconds = per_seconds
        self.timestamps: list[float] = []
        self.lock = threading.Lock()

    async def acquire(self):
        """Acquire a slot — blocks the current thread if rate limit is hit."""
        with self.lock:
            now = time.time()
            self.timestamps = [t for t in self.timestamps if now - t < self.per_seconds]
            if len(self.timestamps) >= self.max_requests:
                sleep_time = self.per_seconds - (now - self.timestamps[0]) + 0.5
                logger.info("Rate limit reached — sleeping %.1fs", sleep_time)
                time.sleep(sleep_time)
            self.timestamps.append(time.time())


# Global rate limiter instance
_rate_limiter = RateLimiter()


# ── OpenAI-compatible client ─────────────────────────────────────

def _get_client() -> OpenAI:
    return OpenAI(base_url=NVIDIA_BASE_URL, api_key=NVIDIA_API_KEY, timeout=120.0)


# ── Rubric Parser ────────────────────────────────────────────────

def parse_rubric(rubric_text: str) -> list[dict]:
    """Parse rubric text to extract criteria and max points.
    
    Handles formats like:
    - correctness:4
    - Attempt: 4 points
    - Correctness (40 points)
    - Correctness: 40
    
    Returns list of dicts with 'criterion' and 'max' keys.
    """
    import re
    
    criteria = []
    lines = rubric_text.strip().split('\n')
    
    for line in lines:
        line = line.strip()
        if not line or line.lower().startswith('total') or line.lower().startswith('max'):
            continue
            
        # Try different patterns
        # Pattern 1: "criterion: 4" or "criterion:4"
        match = re.match(r'^([\w\s]+?)[:\-]\s*(\d+)', line)
        if match:
            criterion = match.group(1).strip()
            points = int(match.group(2))
            criteria.append({"criterion": criterion, "max": points})
            continue
            
        # Pattern 2: "criterion (4 points)" or "criterion (4)"
        match = re.match(r'^([\w\s]+?)\s*\(?\s*(\d+)\s*(?:points?)?\s*\)?', line, re.IGNORECASE)
        if match:
            criterion = match.group(1).strip()
            points = int(match.group(2))
            criteria.append({"criterion": criterion, "max": points})
    
    return criteria


def _validate_and_fix_rubric(result: dict, rubric_dict: dict, max_score: int) -> dict:
    """Validate and fix rubric breakdown to ensure consistency.
    
    Args:
        result: AI grading result dict
        rubric_dict: Dictionary mapping criterion names (lowercase) to max points
        max_score: Total max score for the assignment
    
    Returns:
        Fixed result dict with corrected rubric_breakdown
    """
    if not rubric_dict:
        return result
    
    rubric_breakdown = result.get("rubric_breakdown", [])
    if not rubric_breakdown:
        # If no rubric breakdown, create one from the expected criteria
        rubric_breakdown = []
        for criterion, max_points in rubric_dict.items():
            rubric_breakdown.append({
                "criterion": criterion,
                "score": 0,
                "max": max_points,
                "max_score": max_points,
                "justification": "No specific assessment provided"
            })
        result["rubric_breakdown"] = rubric_breakdown
        return result
    
    # Fix existing rubric breakdown
    fixed_breakdown = []
    used_criteria = set()
    
    for item in rubric_breakdown:
        criterion = item.get("criterion", "").lower().strip()
        
        # Try to match with expected criteria
        matched = False
        for expected_criterion, expected_max in rubric_dict.items():
            if criterion == expected_criterion or expected_criterion in criterion or criterion in expected_criterion:
                # Fix the max value
                item["max"] = expected_max
                item["max_score"] = expected_max
                # Ensure score doesn't exceed max
                score = item.get("score", 0)
                if score > expected_max:
                    item["score"] = expected_max
                fixed_breakdown.append(item)
                used_criteria.add(expected_criterion)
                matched = True
                break
        
        if not matched:
            # Keep the item but ensure it has reasonable max
            current_max = item.get("max", 0)
            if current_max == 0:
                # Try to infer from score or assign reasonable value
                score = item.get("score", 0)
                item["max"] = max(score, 1)  # At least 1 if there's a score
                item["max_score"] = item["max"]
            fixed_breakdown.append(item)
    
    # Add missing criteria
    for criterion, max_points in rubric_dict.items():
        if criterion not in used_criteria:
            # Find score from question_mapping or estimate
            score = max_points  # Assume full credit if not explicitly assessed
            
            # Check if there's any indication of issues in the feedback
            overall_feedback = result.get("overall_feedback", "").lower()
            weaknesses = result.get("weaknesses", [])
            critical_errors = result.get("critical_errors", [])
            
            if weaknesses or critical_errors:
                # Reduce score proportionally
                deduction = len(weaknesses) * 0.5 + len(critical_errors) * 1.0
                score = max(0, max_points - deduction)
            
            fixed_breakdown.append({
                "criterion": criterion,
                "score": round(score, 1),
                "max": max_points,
                "max_score": max_points,
                "justification": f"Assessed based on overall submission quality"
            })
    
    # Recalculate total score from rubric
    total_from_rubric = sum(item.get("score", 0) for item in fixed_breakdown)
    
    # Update result
    result["rubric_breakdown"] = fixed_breakdown
    
    # If total_score is missing or inconsistent, use rubric sum
    current_total = result.get("total_score", 0)
    if current_total == 0 or abs(current_total - total_from_rubric) > 0.5:
        result["total_score"] = round(total_from_rubric, 1)
        result["percentage"] = round((total_from_rubric / max_score) * 100, 1) if max_score > 0 else 0
    
    return result


# ── Prompt construction ──────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert instructor grading student submissions with access to both text and visual content.

Your capabilities include:
- Analyzing code for correctness, style, efficiency, and best practices
- Evaluating written reports for completeness, clarity, and accuracy
- Reviewing images, screenshots, diagrams, and visual outputs
- Assessing handwritten work, sketches, and scanned documents
- Cross-referencing visual content with assignment requirements

GRADING PRINCIPLES:
1. Grade consistently — the same quality of work receives the same grade regardless of evaluation order
2. Map answers explicitly to assignment questions/requirements
3. Use visual evidence when available (screenshots, diagrams, handwritten work)
4. Consider partial credit where appropriate
5. Provide specific, actionable feedback with examples

IMPORTANT: Respond ONLY with valid JSON. No markdown, no code fences, no extra text."""


def _build_user_prompt(
    title: str,
    description: str,
    rubric: str,
    max_score: int,
    student_files: list[dict],
    questions: Optional[list[dict]] = None,
    reference_solution: Optional[str] = None,
    test_results: Optional[str] = None,
) -> str:
    """Build the user prompt for grading.
    
    Args:
        title: Assignment title
        description: Assignment description
        rubric: Grading rubric
        max_score: Maximum possible score
        student_files: List of student submission files with metadata
        questions: Optional list of assignment questions for answer mapping
        reference_solution: Optional reference solution for comparison
        test_results: Optional automated test results
    """
    parts: list[str] = [
        f"ASSIGNMENT: {title}",
        f"MAX SCORE: {max_score}",
        "",
        "=" * 60,
        "ASSIGNMENT DESCRIPTION",
        "=" * 60,
        description,
    ]

    # Add questions if provided for better answer mapping
    if questions:
        parts.extend([
            "",
            "=" * 60,
            "ASSIGNMENT QUESTIONS/REQUIREMENTS",
            "=" * 60,
        ])
        for i, q in enumerate(questions, 1):
            q_text = q.get("text", q.get("question", ""))
            q_points = q.get("points", "")
            parts.append(f"\nQuestion {i}{f' ({q_points} points)' if q_points else ''}:")
            parts.append(q_text)
            if q.get("type"):
                parts.append(f"[Expected response type: {q['type']}]")

    # Parse rubric to get structured criteria
    rubric_criteria = parse_rubric(rubric)
    
    parts.extend([
        "",
        "=" * 60,
        "GRADING RUBRIC",
        "=" * 60,
        rubric,
    ])
    
    # Add structured rubric criteria for clarity
    if rubric_criteria:
        parts.extend([
            "",
            "RUBRIC CRITERIA (with max points):",
        ])
        for item in rubric_criteria:
            parts.append(f"  - {item['criterion']}: {item['max']} points")
        parts.append("")

    if reference_solution:
        parts.extend([
            "",
            "=" * 60,
            "REFERENCE SOLUTION (for comparison)",
            "=" * 60,
            reference_solution,
        ])

    if test_results:
        parts.extend([
            "",
            "=" * 60,
            "AUTOMATED TEST RESULTS",
            "=" * 60,
            test_results,
        ])

    # Document what files are included
    parts.extend([
        "",
        "=" * 60,
        "STUDENT SUBMISSION FILES",
        "=" * 60,
        f"Total files submitted: {len(student_files)}\n",
    ])

    text_content_files = []
    vision_files = []

    for f in student_files:
        file_type = f.get("type", "unknown")
        filename = f.get("filename", "unknown")
        
        if file_type in ("code", "text", "docx", "notebook", "csv", "json", "xml", "md"):
            text_content_files.append(f)
            parts.append(f"📄 {filename} ({file_type}) — Text content included below")
        elif file_type == "pdf_text":
            text_content_files.append(f)
            parts.append(f"📄 {filename} (PDF — extracted text included below)")
        elif file_type == "image":
            vision_files.append(f)
            media_type = f.get("media_type", "image/png")
            parts.append(f"🖼️  {filename} ({media_type}) — Image sent separately for vision analysis")
        elif file_type == "pdf_images":
            vision_files.append(f)
            page_count = len(f.get("content", [])) if isinstance(f.get("content"), list) else 1
            parts.append(f"📑 {filename} (PDF — {page_count} page(s) sent as images for vision analysis)")
        elif file_type == "unsupported":
            parts.append(f"⚠️  {filename} (unsupported file type — skipped)")
        elif file_type == "error":
            parts.append(f"❌ {filename} (parse error: {f.get('error', 'unknown')})")
        else:
            text_content_files.append(f)
            parts.append(f"📄 {filename} ({file_type}) — Content included below")

    # Add text content from files
    if text_content_files:
        parts.extend([
            "",
            "-" * 60,
            "FILE CONTENTS (TEXT)",
            "-" * 60,
        ])
        for f in text_content_files:
            filename = f.get("filename", "unknown")
            content = f.get("content", "")
            if content:
                parts.append(f"\n>>> {filename} <<<")
                parts.append(content)
                parts.append(f"<<< END {filename} >>>\n")

    # Note about vision files
    if vision_files:
        parts.extend([
            "",
            "-" * 60,
            "VISION ANALYSIS FILES",
            "-" * 60,
            "The following files have been sent as images for visual analysis:",
        ])
        for f in vision_files:
            filename = f.get("filename", "unknown")
            file_type = f.get("type", "unknown")
            if file_type == "pdf_images":
                page_count = len(f.get("content", [])) if isinstance(f.get("content"), list) else 1
                parts.append(f"  • {filename} — {page_count} page(s)")
            else:
                parts.append(f"  • {filename}")

    parts.extend([
        "",
        "=" * 60,
        "GRADING INSTRUCTIONS",
        "=" * 60,
        """
1. QUESTION-TO-ANSWER MAPPING:
   - Map each student response to the corresponding assignment question
   - Identify which files answer which questions
   - Note any missing or incomplete responses

2. FILE TYPE EVALUATION:
   
   FOR CODE FILES:
   - Check correctness, logic, and algorithm efficiency
   - Evaluate code style, readability, and documentation
   - Assess error handling and edge cases
   - Verify adherence to language best practices
   
   FOR WRITTEN DOCUMENTS:
   - Check completeness against assignment requirements
   - Evaluate clarity, organization, and flow
   - Verify factual accuracy and depth of understanding
   - Assess grammar, spelling, and formatting
   
   FOR IMAGES & SCREENSHOTS:
   - Verify visual output matches expected results
   - Check UI/UX elements if applicable
   - Confirm diagrams are clear and properly labeled
   - Look for visual evidence of functionality
   
   FOR HANDWRITTEN WORK:
   - Verify mathematical derivations and calculations
   - Check diagrams, sketches, and drawings
   - Assess legibility and organization
   - Look for correct methodology and reasoning
   
   FOR PDFs:
   - Review both extracted text and page images
   - Cross-reference text content with visual layout
   - Check for scanned handwritten content
   - Verify all pages are present and readable

3. RUBRIC APPLICATION:
   - Grade STRICTLY according to the provided rubric
   - Award partial credit where justified
   - Document specific point deductions with evidence
   - Consider both objective correctness and subjective quality

4. CROSS-CHECKING:
   - If reference solution is provided, compare approaches
   - If test results are available, weight them heavily for correctness
   - Verify consistency across multiple files
   - Check for plagiarism or academic dishonesty red flags

5. SCORING GUIDELINES:
   - Calculate scores based on rubric criteria
   - Ensure total doesn't exceed max_score
   - Round to nearest 0.5 or whole number as appropriate
   - Consider extra credit only if explicitly allowed
""",
        "",
        "=" * 60,
        "REQUIRED RESPONSE FORMAT",
        "=" * 60,
        """Respond in this exact JSON format:

{
  "total_score": <number>,
  "max_score": <number>,
  "letter_grade": "<A+/A/A-/B+/B/B-/C+/C/C-/D+/D/F>",
  "percentage": <number>,
  
  "rubric_breakdown": [
    {
      "criterion": "<exact rubric criterion name from the RUBRIC CRITERIA section above>",
      "category": "<category if applicable>",
      "score": <number - points earned>,
      "max": <number - MUST be the exact max points from the RUBRIC CRITERIA section, e.g., if Correctness is 4 points, use 4>,
      "weight": <number>,
      "justification": "<detailed explanation with specific evidence from submission>",
      "files_evaluated": ["<filename1>", "<filename2>"]
    }
  ],
  
  "question_mapping": [
    {
      "question_number": <number>,
      "question_text": "<brief question summary>",
      "files_addressing": ["<filename1>", "<filename2>"],
      "response_summary": "<summary of student's answer>",
      "correctness": "<correct/partially_correct/incorrect/not_addressed>",
      "score": <number>,
      "max_points": <number>,
      "feedback": "<specific feedback for this question>"
    }
  ],
  
  "file_analysis": [
    {
      "filename": "<name>",
      "file_type": "<type>",
      "assessment": "<detailed analysis of this specific file>",
      "strengths": ["<specific strength with line/page reference>"],
      "issues_found": [
        {
          "severity": "<critical/major/minor>",
          "description": "<detailed issue description>",
          "location": "<line number, page, or section>",
          "suggestion": "<how to fix>"
        }
      ],
      "questions_addressed": [<question_numbers>],
      "contribution_to_grade": "<how this file impacts overall score>"
    }
  ],
  
  "overall_feedback": "<comprehensive 6-10 sentence assessment explaining the overall grade, patterns observed, and general evaluation>",
  
  "strengths": [
    "<specific strength with evidence: 'Student demonstrated X by doing Y in file Z'>"
  ],
  
  "weaknesses": [
    "<specific weakness with evidence: 'Student failed to address X as seen in file Y'>"
  ],
  
  "critical_errors": [
    "<any major errors: compilation failures, completely wrong logic, missing requirements, etc.>"
  ],
  
  "suggestions_for_improvement": "<detailed, actionable advice for the student to improve future submissions>",
  
  "visual_content_analysis": {
    "images_reviewed": [<number_of_images>],
    "key_observations": ["<notable visual elements or patterns>"],
    "visual_accuracy": "<assessment of visual correctness if applicable>"
  },
  
  "confidence": "<high/medium/low>",
  "confidence_reasoning": "<explanation of confidence level>"
}

IMPORTANT NOTES:
- Be specific and cite evidence from the submission
- Use actual filenames and line numbers where applicable
- If images/diagrams are present, describe what they show
- Explain your reasoning for partial credit decisions
- Note any ambiguities or unclear aspects that affected grading

RUBRIC SCORING RULES (CRITICAL):
1. The "max" field in rubric_breakdown MUST match the max points from RUBRIC CRITERIA section exactly
2. If Correctness is worth 4 points, use "max": 4, NOT "max": 0 or any other value
3. The "score" field should be points earned (0 to max), e.g., if student got 3.5/4, use "score": 3.5, "max": 4
4. Sum of all rubric_breakdown scores should equal total_score
5. Double-check that max values match the rubric before responding""",
    ])

    return "\n".join(parts)


# ── Vision/Image Helpers ─────────────────────────────────────────

def _build_multimodal_content(
    user_text: str,
    student_files: list[dict],
    max_images: int = 50,
) -> tuple[list[dict], bool]:
    """Build multimodal content with text and images.
    
    Args:
        user_text: The text prompt
        student_files: List of student files with potential image content
        max_images: Maximum number of images to include (to avoid token limits)
    
    Returns:
        Tuple of (content_list, has_images)
    """
    content: list[dict] = [{"type": "text", "text": user_text}]
    image_count = 0
    has_images = False

    for f in student_files:
        if image_count >= max_images:
            logger.warning(f"Reached max image limit ({max_images}), skipping remaining images")
            break

        file_type = f.get("type")
        
        # Handle single images (PNG, JPG, etc.)
        if file_type == "image" and f.get("content"):
            media_type = f.get("media_type", "image/png")
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{media_type};base64,{f['content']}",
                    "detail": "high"  # Request high detail for better grading accuracy
                },
            })
            image_count += 1
            has_images = True
            logger.debug(f"Added image: {f.get('filename', 'unknown')} ({media_type})")

        # Handle PDF converted to images
        elif file_type == "pdf_images" and isinstance(f.get("content"), list):
            for i, img_b64 in enumerate(f["content"]):
                if image_count >= max_images:
                    logger.warning(f"Reached max image limit ({max_images}), skipping remaining PDF pages")
                    break
                
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{img_b64}",
                        "detail": "high"
                    },
                })
                image_count += 1
                has_images = True
                logger.debug(f"Added PDF page {i+1} from: {f.get('filename', 'unknown')}")

    return content, has_images


def _classify_file_types(student_files: list[dict]) -> dict:
    """Classify and summarize the types of files in submission.
    
    Args:
        student_files: List of student submission files
    
    Returns:
        Dictionary with file type statistics
    """
    stats = {
        "code_files": [],
        "text_files": [],
        "images": [],
        "pdfs_text": [],
        "pdfs_vision": [],
        "unsupported": [],
        "errors": [],
        "total": len(student_files),
    }

    for f in student_files:
        file_type = f.get("type", "unknown")
        filename = f.get("filename", "unknown")

        if file_type in ("code", "notebook"):
            stats["code_files"].append(filename)
        elif file_type in ("text", "docx", "csv", "json", "xml", "md"):
            stats["text_files"].append(filename)
        elif file_type == "image":
            stats["images"].append(filename)
        elif file_type == "pdf_text":
            stats["pdfs_text"].append(filename)
        elif file_type == "pdf_images":
            stats["pdfs_vision"].append(filename)
        elif file_type == "unsupported":
            stats["unsupported"].append(filename)
        elif file_type == "error":
            stats["errors"].append(filename)
        else:
            stats["text_files"].append(filename)

    return stats


# ── Main grading function ────────────────────────────────────────

async def grade_student(
    title: str,
    description: str,
    rubric: str,
    max_score: int,
    student_files: list[dict],
    questions: Optional[list[dict]] = None,
    reference_solution: Optional[str] = None,
    test_results_str: Optional[str] = None,
    max_images: int = 50,
) -> dict[str, Any]:
    """Grade a single student's submission via the AI API.
    
    Supports multimodal inputs including text, code, images, and PDFs.
    For PDFs, can process both extracted text and converted page images.

    Args:
        title: Assignment title
        description: Assignment description/instructions
        rubric: Grading rubric (can be text or structured)
        max_score: Maximum points possible
        student_files: List of dicts with file info including:
            - filename: Name of the file
            - type: File type (code, image, pdf_text, pdf_images, etc.)
            - content: File content (text or base64 for images)
            - media_type: For images (image/png, image/jpeg, etc.)
        questions: Optional list of assignment questions for answer mapping
        reference_solution: Optional reference solution text
        test_results_str: Optional automated test results
        max_images: Maximum images to send (default 50 to avoid token limits)

    Returns:
        Parsed JSON result dict or error dict with error details
    """
    await _rate_limiter.acquire()

    # Classify files for logging and debugging
    file_stats = _classify_file_types(student_files)
    logger.info(
        f"Grading submission for '{title}': "
        f"{len(file_stats['code_files'])} code, "
        f"{len(file_stats['text_files'])} text, "
        f"{len(file_stats['images'])} images, "
        f"{len(file_stats['pdfs_text'])} PDFs (text), "
        f"{len(file_stats['pdfs_vision'])} PDFs (vision), "
        f"{len(file_stats['unsupported'])} unsupported"
    )

    # Parse rubric to get expected criteria and max points
    rubric_criteria = parse_rubric(rubric)
    rubric_dict = {item['criterion'].lower().strip(): item['max'] for item in rubric_criteria}
    
    client = _get_client()
    
    # Build the text prompt
    user_text = _build_user_prompt(
        title=title,
        description=description,
        rubric=rubric,
        max_score=max_score,
        student_files=student_files,
        questions=questions,
        reference_solution=reference_solution,
        test_results=test_results_str,
    )

    # Build messages with multimodal support
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Build multimodal content (text + images)
    user_content, has_images = _build_multimodal_content(
        user_text=user_text,
        student_files=student_files,
        max_images=max_images,
    )

    if has_images:
        messages.append({"role": "user", "content": user_content})
        logger.info(f"Sending multimodal request with {sum(1 for c in user_content if c.get('type') == 'image_url')} images")
    else:
        messages.append({"role": "user", "content": user_text})
        logger.info("Sending text-only request")

    # Call the API (with one retry on failure)
    raw_text = ""
    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=NVIDIA_MODEL,
                messages=messages,
                temperature=0.1,
                max_tokens=4096,
            )
            raw_text = response.choices[0].message.content.strip()
            
            # Strip markdown code fences if present
            if raw_text.startswith("```"):
                raw_text = raw_text.split("\n", 1)[1] if "\n" in raw_text else raw_text[3:]
                if raw_text.endswith("```"):
                    raw_text = raw_text[:-3].strip()

            result = json.loads(raw_text)
            result["_raw_response"] = raw_text
            result["_file_stats"] = file_stats
            result["_multimodal"] = has_images
            
            # Validate and fix rubric breakdown
            result = _validate_and_fix_rubric(result, rubric_dict, max_score)
            
            logger.info(f"Grading complete: score={result.get('total_score', 'N/A')}/{max_score}")
            return result

        except json.JSONDecodeError as e:
            logger.warning("JSON parse error (attempt %d): %s", attempt + 1, e)
            if attempt == 0:
                await _rate_limiter.acquire()
                continue
            return {
                "error": f"Failed to parse AI response as JSON: {e}",
                "_raw_response": raw_text,
                "_file_stats": file_stats,
                "total_score": None,
                "max_score": max_score,
                "confidence": "low",
            }
            
        except Exception as e:
            logger.exception("AI API error (attempt %d): %s", attempt + 1, e)
            if attempt == 0:
                await asyncio.sleep(2)
                continue
            return {
                "error": f"AI API call failed: {e}",
                "_file_stats": file_stats,
                "total_score": None,
                "max_score": max_score,
                "confidence": "low",
            }

    # Should never reach here, but just in case
    return {
        "error": "Grading failed after retries",
        "_file_stats": file_stats,
        "total_score": None,
        "max_score": max_score,
        "confidence": "low",
    }


# ── Batch Grading Helper ─────────────────────────────────────────

async def grade_multiple_students(
    title: str,
    description: str,
    rubric: str,
    max_score: int,
    student_submissions: list[dict],
    questions: Optional[list[dict]] = None,
    reference_solution: Optional[str] = None,
    max_images: int = 50,
) -> list[dict[str, Any]]:
    """Grade multiple student submissions.
    
    Args:
        title: Assignment title
        description: Assignment description
        rubric: Grading rubric
        max_score: Maximum score
        student_submissions: List of dicts with 'student_id' and 'files' keys
        questions: Optional list of assignment questions
        reference_solution: Optional reference solution
        max_images: Maximum images per submission

    Returns:
        List of grading results corresponding to each submission
    """
    results = []
    
    for i, submission in enumerate(student_submissions, 1):
        student_id = submission.get("student_id", f"student_{i}")
        files = submission.get("files", [])
        test_results = submission.get("test_results")
        
        logger.info(f"Grading submission {i}/{len(student_submissions)}: {student_id}")
        
        result = await grade_student(
            title=title,
            description=description,
            rubric=rubric,
            max_score=max_score,
            student_files=files,
            questions=questions,
            reference_solution=reference_solution,
            test_results_str=test_results,
            max_images=max_images,
        )
        
        result["student_id"] = student_id
        result["submission_index"] = i
        results.append(result)
    
    return results
