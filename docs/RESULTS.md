# Evaluation results — `test` split (40 cases)

Provider: `openrouter` · prompt version `v1` · temperature 0 · every number below is recomputed by CI from the recorded responses in `cassettes/`.

## Model selection

| Model | Area | Deadline exact | Unsafe deadline (silent) | Flag F1 | Queue | Valid JSON 1st try | Draft passes guardrails | Injection success raw → after guardrail | Cost / case | p50 / p95 latency | Gates |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `openai/gpt-4.1-mini` | 82.5% | 88.0% | 3 (2) | 0.789 | 82.5% | 100.0% | 97.1% | 0/10 → 0/10 | $0.0016 | 4.91s / 6.03s | ❌ 4 |

**Recommendation:** no model passes every gate; see failures below.

### Gate failures

- `openai/gpt-4.1-mini`: no_silent_unsafe_deadline: unsafe_deadlines_silent=2 > 0; area_accuracy: area_accuracy=0.825 < 0.9; flag_f1: flag_f1=0.7895 < 0.8; extraction_success: extraction_success=0.85 < 0.95

### Detail

| Model | Event type | Receipt date (absolute / relative) | Dispute date | Date tool used on relative dates | Spurious deadlines | LLM calls / case | Tokens / case | Total cost |
|---|---|---|---|---|---|---|---|---|
| `openai/gpt-4.1-mini` | 85.0% | 90.0% / 80.0% | 69.2% | 80.0% | 0 | 1.93 | 2397 | $0.057 |

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
| LLM calls `shift_days` / `find_weekday` | 80.0% | – |

## Personal data

- Blind first run of the pseudonymiser on the frozen test split: **2/159** PII strings leaked (1.3%) — both were employer names without a legal-form suffix.
- After adding a trade-name rule (informed by that run, so no longer blind): 0 leaks; CI keeps this at 0.
- End-to-end, measured on every payload actually sent to a model: see `pii_leak_rate` per model above (openai/gpt-4.1-mini: 0.0%).
