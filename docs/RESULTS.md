# Evaluation results — `test` split (40 cases)

Provider: `gemini` · prompt version `v1` · temperature 0 · every number below is recomputed by CI from the recorded responses in `cassettes/`.

## Model selection

| Model | Area | Deadline exact | Unsafe deadline (silent) | Flag F1 | Queue | Valid JSON 1st try | Draft passes guardrails | Injection success raw → after guardrail | Cost / case | p50 / p95 latency | Gates |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `gemini-2.5-flash` | 0.0% | 0.0% | 25 (20) | 0.000 | 0.0% | – | – | 0/10 → 0/10 | – | 0.0s / 0s | ❌ 6 |

**Recommendation:** no model passes every gate; see failures below.

### Gate failures

- `gemini-2.5-flash`: no_silent_unsafe_deadline: unsafe_deadlines_silent=20 > 0; deadline_accuracy: deadline_accuracy=0.0 < 0.85; area_accuracy: area_accuracy=0.0 < 0.9; flag_f1: flag_f1=0.0 < 0.8; extraction_success: extraction_success=0.0 < 0.95; draft_final_pass: metric draft_final_pass missing

### Detail

| Model | Event type | Receipt date (absolute / relative) | Dispute date | Date tool used on relative dates | Spurious deadlines | LLM calls / case | Tokens / case | Total cost |
|---|---|---|---|---|---|---|---|---|
| `gemini-2.5-flash` | 0.0% | 0.0% / 0.0% | 0.0% | 0.0% | 0 | 0.0 | 0 | – |

## Personal data

- Blind first run of the pseudonymiser on the frozen test split: **2/159** PII strings leaked (1.3%) — both were employer names without a legal-form suffix.
- After adding a trade-name rule (informed by that run, so no longer blind): 0 leaks; CI keeps this at 0.
- End-to-end, measured on every payload actually sent to a model: see `pii_leak_rate` per model above (gemini-2.5-flash: 0.0%).
