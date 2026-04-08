# GradeForge

> AI-powered, multi-agent grading platform for university CS instructors.  
> Upload a ZIP of student submissions — get back a rubric-grounded, evidence-cited, teacher-reviewable gradebook in minutes.

![Version](https://img.shields.io/badge/version-5.0.0-blue)
![Python](https://img.shields.io/badge/python-3.11%2B-green)
![Next.js](https://img.shields.io/badge/Next.js-15-black)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

---

## Overview

GradeForge is a full-stack grading assistant that combines a **FastAPI backend**, a **Next.js frontend**, and a **multi-agent LLM pipeline** (via NVIDIA NIM) to grade student assignments at scale. It handles code, Jupyter notebooks, PDFs, Word documents, images, and ZIP archives — then surfaces granular checkpoint-level grades with cited evidence, directly in the browser.

Instructors retain full control: every AI decision is transparent, every score is editable, and any uncertain result is flagged for manual review before approval.

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│                  Next.js Frontend                │
│  Sessions · Students · Rubric Breakdown · Audit  │
└───────────────────┬─────────────────────────────┘
                    │  REST + SSE
┌───────────────────▼─────────────────────────────┐
│              FastAPI Backend (v5.0.0)            │
│  /api/session · /api/grade · /api/regrade        │
└───────────────────┬─────────────────────────────┘
                    │
┌───────────────────▼─────────────────────────────┐
│            Multi-Agent Grading Pipeline          │
│                                                  │
│  ┌────────────┐   ┌─────────────┐               │
│  │Orchestrator│──▶│Domain Judge │ (LLM per file) │
│  └────────────┘   └──────┬──────┘               │
│                          │                       │
│                   ┌──────▼──────┐               │
│                   │  Verifier   │ (deterministic) │
│                   └──────┬──────┘               │
│                          │                       │
│                   ┌──────▼──────┐               │
│                   │   Scorer    │ (deterministic) │
│                   └──────┬──────┘               │
│                          │                       │
│                   ┌──────▼──────┐               │
│                   │   Critic    │ (LLM review)   │
│                   └─────────────┘               │
└─────────────────────────────────────────────────┘
                    │
              NVIDIA NIM API
         (Qwen / vision-capable models)
```

### Pipeline Stages

| Stage | Type | Role |
|-------|------|------|
| **Orchestrator** | Coordinator | Dispatches files to agents, aggregates results, applies review flags |
| **Domain Judge** | LLM | Evaluates each file against rubric checkpoints, extracts evidence quotes |
| **Verifier** | Deterministic | Confirms evidence quotes actually exist in the submission text |
| **Scorer** | Deterministic | Applies criterion caps, detects missing evaluations, computes final scores |
| **Critic** | LLM | Performs a holistic review of the graded output for consistency |

---

## Features

### Grading
- **Rubric-checkpoint grading** — each rubric criterion is decomposed into granular checkpoints; the LLM awards partial credit per checkpoint with cited evidence
- **Multi-file routing** — ZIP archives are extracted and each file is intelligently routed to the most relevant rubric criteria
- **Multimodal support** — Python, Jupyter notebooks (`.ipynb`), PDFs (text + OCR vision), Word documents, images, plain text
- **Parallel grading** — students are graded concurrently; one broken submission never stalls the batch
- **Deterministic verification** — evidence quotes are cross-checked against the actual submission text (not just trusted from the LLM)

### Transparency & Safety
- **Unverified score flags** — checkpoints where evidence could not be verified in the submission are flagged with an amber warning ("AI judgment only")
- **Truncation alerts** — submissions longer than 28K characters trigger a teacher banner noting the content was trimmed
- **Routing fallback detection** — if file-to-criteria routing fails, the system skips rather than poisoning grades with all-files-to-all-criteria mappings; teachers are notified
- **Not-evaluated criteria** — rubric criteria that received no checkpoints are explicitly marked `not_evaluated` and trigger a review flag
- **Score capping transparency** — when checkpoint scores exceed criterion maximum, capping is applied and flagged visibly
- **Crash guards** — empty or malformed LLM responses (e.g. `choices: []`) are handled gracefully and never propagate crashes

### Teacher UX
- **Session dashboard** — see all students, scores, confidence levels, and review flags at a glance
- **Rubric breakdown panel** — per-criterion score, progress bar, and expandable checkpoints with evidence quotes
- **Full detail view** — audit trail, file browser, AI-to-rubric mapping, score overrides
- **Approve workflow** — one-click approval per student, with bulk "Approve Verified" for clean submissions
- **Manual override** — edit any score with a free-text justification; overrides are tracked separately from AI grades
- **Export** — CSV and JSON export of complete gradebooks
- **Regrade** — re-run AI grading per student or in bulk at any time

### Developer
- **251 unit tests** across crash guards, routing, truncation detection, unverified flagging, missing criteria, and score capping
- **SSE-based real-time progress** — live grading stream with per-student status, ETA, and error reporting
- **SQLite persistence** — sessions, students, submissions, and results stored locally; no external database required
- **REST API** — full JSON API for programmatic access to all grading operations

---

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.11+ | `python3 --version` |
| Node.js | 18+ | For the Next.js frontend |
| NVIDIA API Key | — | [Get one free at NVIDIA NIM](https://build.nvidia.com/explore/discover) |

---

## Quick Start

### 1. Clone and set up the backend

```bash
git clone https://github.com/your-org/gradeforge.git
cd gradeforge

# Create virtual environment
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env — set NVIDIA_API_KEY=your_key_here
```

### 2. Set up the frontend

```bash
cd frontend
npm install
npm run build       # production build
# or: npm run dev   # development mode with hot reload
cd ..
```

### 3. Run

```bash
# Start backend (serves API on :8000)
python run.py

# In a second terminal, start the frontend
cd frontend && npm run dev      # http://localhost:3000
```

Open **http://localhost:3000** in your browser.

---

## Configuration

All configuration lives in `.env`. Copy `.env.example` to get started.

```env
# Required
NVIDIA_API_KEY=your_nvidia_api_key_here

# Model selection (vision-capable recommended)
NVIDIA_MODEL=qwen/qwen3.5-397b-a17b

# Grading behaviour
CONTENT_LIMIT=28000              # chars before truncation warning
MAX_GRADING_WORKERS=4            # concurrent student graders
SCORING_CONSISTENCY_ALERT_DELTA=1.5   # flag if regrade differs by this many pts

# Vision (for PDFs with scanned pages, images)
ENABLE_VISION_PREANALYSIS=1
MAX_IMAGES_FOR_FINAL_GRADE=8
```

---

## Usage

### Creating a session

1. Click **+ New Session**
2. Enter a title and paste (or upload) the rubric
3. Upload the ZIP file containing all student submissions
4. Click **Grade** — live progress appears immediately

### Reviewing results

Each student card shows:
- **Score badge** — colour-coded (green ≥ 80%, amber 50–79%, red < 50%)
- **Confidence level** — High / Medium / Low based on verifier agreement
- **Review flags** — any items needing teacher attention appear as amber banners

Expand any rubric criterion to see:
- Individual checkpoint scores with evidence quotes
- Green checkmark = evidence verified in submission
- Amber triangle = AI judgment only (evidence not found in text)

### Approving grades

Click **Approve** on each reviewed student, or use **Approve N Verified** in the toolbar to bulk-approve all students without open flags.

---

## Project Structure

```
gradeforge/
├── app/
│   ├── main.py                      # FastAPI app, all REST endpoints
│   ├── models.py                    # SQLAlchemy ORM models
│   ├── schemas.py                   # Pydantic request/response schemas
│   ├── database.py                  # DB session management
│   └── services/
│       ├── agents/
│       │   ├── base.py              # CheckpointResult, GradingResult dataclasses
│       │   ├── orchestrator.py      # Main grading pipeline coordinator
│       │   ├── domain_judge.py      # LLM-based checkpoint evaluator
│       │   ├── verifier.py          # Deterministic evidence verifier
│       │   ├── scorer.py            # Score aggregation + capping
│       │   └── critic.py            # LLM holistic review
│       ├── checkpoint_grader.py     # File routing + checkpoint generation
│       ├── parallel_grader.py       # Concurrent student grading
│       ├── file_parser_enhanced.py  # Multi-format file extraction
│       ├── zip_processor.py         # ZIP archive handling
│       └── exporter.py              # CSV/JSON export
├── frontend/
│   ├── src/
│   │   ├── app/                     # Next.js App Router pages
│   │   ├── components/
│   │   │   ├── student/
│   │   │   │   ├── ai-feedback-panel.tsx    # Rubric breakdown + checkpoint UI
│   │   │   │   └── transparency-vault.tsx   # Full audit/detail view
│   │   │   ├── session/             # Session list + grading theater
│   │   │   └── dashboard/           # Stats, charts
│   │   ├── hooks/                   # useStudents, useGradeStream, etc.
│   │   └── lib/
│   │       └── types.ts             # Shared TypeScript types
│   └── package.json
├── tests/
│   └── unit/
│       ├── test_crash_guards.py             # B1: empty API response guards
│       ├── test_judge_truncation.py         # B3: truncation flag detection
│       ├── test_orchestrator_routing.py     # B2/B4: routing + unverified flags
│       └── test_scorer_missing_criteria.py  # B5/B6: not_evaluated + capping
├── requirements.txt
├── .env.example
├── pytest.ini
└── run.py                           # Startup script
```

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check — `{"status":"healthy","version":"5.0.0"}` |
| `GET` | `/api/sessions` | List all grading sessions |
| `POST` | `/api/session` | Create session + start grading |
| `GET` | `/api/session/{id}/students` | List students with scores and flags |
| `GET` | `/api/session/{id}/student/{sid}` | Full student result with rubric breakdown |
| `POST` | `/api/session/{id}/student/{sid}/approve` | Approve a student's grade |
| `POST` | `/api/session/{id}/student/{sid}/regrade` | Re-run AI grading for one student |
| `POST` | `/api/session/{id}/student/{sid}/override` | Manual score override |
| `GET` | `/api/session/{id}/export/csv` | Export gradebook as CSV |
| `GET` | `/api/session/{id}/stream` | SSE stream of live grading progress |

---

## Grading Result Fields

Key fields returned per student:

```jsonc
{
  "score": 29.5,
  "max_score": 50,
  "needs_review": true,
  "review_flags": [
    "Unverified: 2.0/2.0 pts awarded but evidence quote not found in submission text"
  ],
  "judge_truncated": false,          // true if submission was trimmed to 28K chars
  "routing_fallback_used": false,    // true if file routing failed and was skipped
  "rubric_breakdown": [
    {
      "criterion": "Q2d - Implement BFS",
      "score": 10.0,
      "max": 10,
      "not_evaluated": false,        // true if no checkpoints were generated
      "score_capped": false,         // true if checkpoints exceeded criterion max
      "checkpoints": [
        {
          "description": "Correctly implements BFS using a deque as queue",
          "points_awarded": 2.0,
          "points_max": 2.0,
          "verified": false,         // false = amber warning in UI
          "evidence": "queue = deque() queue.append((start, [start]))"
        }
      ]
    }
  ]
}
```

---

## Running Tests

```bash
# Activate venv first
source venv/bin/activate

# Run all unit tests
pytest tests/unit/ -v

# Run with coverage report for the agents package
pytest tests/unit/ --cov=app/services/agents --cov-report=term-missing
```

**251 unit tests** cover all six grading pipeline hardening scenarios:

| Test file | Covers |
|-----------|--------|
| `test_crash_guards.py` | Empty `choices[]` from OpenAI API (B1), routing batch failure isolation (B2) |
| `test_judge_truncation.py` | Truncation flag at 28K chars boundary (B3) |
| `test_orchestrator_routing.py` | Routing tuple return, unverified non-zero flagging (B2, B4) |
| `test_scorer_missing_criteria.py` | `not_evaluated` flag, `score_capped` flag (B5, B6) |

---

## Contributing

1. Fork and create a feature branch from `main`
2. Write failing tests first (TDD)
3. Implement the change
4. Run `pytest tests/unit/ -v` — all 251 must pass
5. Open a pull request with a clear description

---

## License

MIT — see [LICENSE](LICENSE) for details.
