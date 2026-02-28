# Ollama Setup (Primary Scoring)

This project now uses Ollama as the default primary provider, with Groq/OpenRouter/NVIDIA as failover.

## 1) Start Ollama

Local daemon:

```bash
ollama serve
```

Cloud-backed model registration example:

```bash
ollama run kimi-k2.5:cloud
```

## 2) Configure `.env`

```dotenv
LLM_PROVIDER_ORDER=ollama,groq,openrouter,nvidia

OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_API_KEY=
OLLAMA_MODEL_TEXT=kimi-k2.5:cloud
OLLAMA_MODEL_VISION=kimi-k2.5:cloud
OLLAMA_MAX_IMAGES_PER_REQUEST=14

SCORING_PRIMARY_PROVIDER=ollama
SCORING_ALLOW_FALLBACK=1
SCORING_CONSISTENCY_ALERT_DELTA=1.5
```

Optional failover keys:

```dotenv
GROQ_API_KEY=...
OPENROUTER_API_KEY=...
NVIDIA_API_KEY=...
```

## 3) Run the app

```bash
./venv/bin/python run.py
```

Then open `http://localhost:8000`.

## 4) Health checks

Quick local provider check:

```bash
curl -s http://localhost:11434/api/tags
```

Quick app check:

```bash
curl -s http://localhost:8000/health
```

## 5) Periodic grading flow

1. Keep Ollama running (`ollama serve`).
2. Start app (`./venv/bin/python run.py`).
3. Upload ZIP and trigger grading from UI.
4. Export CSV/JSON after completion.
5. Regrade only flagged students if `consistency_alert` appears.

## 6) Shutdown

Stop app with `Ctrl+C`.
Stop Ollama process (`Ctrl+C` in the Ollama terminal, or stop its service).

