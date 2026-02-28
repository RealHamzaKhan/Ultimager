# Grading Consistency Policy

This system is tuned for stable grading across re-runs.

## Determinism controls

- Fixed scoring temperature: `0.0`
- Fixed seed: `42`
- Fixed rubric schema validation before score acceptance
- Grading hash includes rubric + parsed submission + provider signature

## Provider behavior

- Primary scorer: `SCORING_PRIMARY_PROVIDER` (default `ollama`)
- Fallback allowed: `SCORING_ALLOW_FALLBACK=1`
- Provider failover is explicit and recorded in transparency:
  - `transparency.llm_call.preferred_provider`
  - `transparency.llm_call.fallback_used`
  - `transparency.llm_call.fallback_attempts`

## Regrade drift guard

On regrade, the result now records:

- `transparency.llm_call.regrade_previous_score`
- `transparency.llm_call.regrade_score_delta`
- `transparency.llm_call.consistency_alert`

Alert threshold is controlled by:

```dotenv
SCORING_CONSISTENCY_ALERT_DELTA=1.5
```

If drift exceeds threshold, review evidence panel and rubric citations before overriding.

