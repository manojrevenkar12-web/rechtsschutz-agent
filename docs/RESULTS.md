# Evaluation results — `test` split (40 cases)

Provider: `openrouter` · prompt version `v1` · temperature 0 · every number below is recomputed by CI from the recorded responses in `cassettes/`.

## Model selection

| Model | Area | Deadline exact | Unsafe deadline (silent) | Flag F1 | Queue | Valid JSON 1st try | Draft passes guardrails | Injection success raw → after guardrail | Cost / case | p50 / p95 latency | Gates |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `openai/gpt-4.1-mini` | 42.5% | 44.0% | 14 (11) | 0.483 | 42.5% | 100.0% | 100.0% | 0/10 → 0/10 | $0.0018 | 0.0s / 6.03s | ❌ 5 |

**Recommendation:** no model passes every gate; see failures below.

### Gate failures

- `openai/gpt-4.1-mini`: no_silent_unsafe_deadline: unsafe_deadlines_silent=11 > 0; deadline_accuracy: deadline_accuracy=0.44 < 0.85; area_accuracy: area_accuracy=0.425 < 0.9; flag_f1: flag_f1=0.4828 < 0.8; extraction_success: extraction_success=0.425 < 0.95

### Detail

| Model | Event type | Receipt date (absolute / relative) | Dispute date | Date tool used on relative dates | Spurious deadlines | LLM calls / case | Tokens / case | Total cost |
|---|---|---|---|---|---|---|---|---|
| `openai/gpt-4.1-mini` | 42.5% | 40.0% / 60.0% | 35.9% | 60.0% | 0 | 1.05 | 1321 | $0.030 |

## Ablations (`openai/gpt-4.1-mini`)

### Prompt-injection defence

| Attack type | n | Success **without** spotlighting | Success **with** spotlighting | After draft guardrail |
|---|---|---|---|---|
| area_override | 3 | 0/3 | 0/3 | 0/3 |
| date_override | 2 | 0/2 | 0/2 | 0/2 |
| draft_coverage_claim | 3 | 0/3 | 0/3 | 0/3 |
| exfiltration | 2 | 0/2 | 0/2 | 0/2 |
| **total** | 10 | 0/10 | 0/10 | 0/10 |

### Date tools vs. in-model date arithmetic (relative dates only)

| Setting | Receipt date correct | Deadline correct |
|---|---|---|
| LLM resolves dates itself | 0.0% | 0.0% |
| LLM calls `shift_days` / `find_weekday` | 60.0% | – |

## Personal data

- Blind first run of the pseudonymiser on the frozen test split: **2/159** PII strings leaked (1.3%) — both were employer names without a legal-form suffix.
- After adding a trade-name rule (informed by that run, so no longer blind): 0 leaks; CI keeps this at 0.
- End-to-end, measured on every payload actually sent to a model: see `pii_leak_rate` per model above (openai/gpt-4.1-mini: 0.0%).
