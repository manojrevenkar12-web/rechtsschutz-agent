# Evaluation results — `test` split (40 cases)

Provider: `gemini` · prompt version `v1` · temperature 0 · every number below is recomputed by CI from the recorded responses in `cassettes/`.

## Model selection

| Model | Area | Deadline exact | Unsafe deadline (silent) | Flag F1 | Queue | Valid JSON 1st try | Draft passes guardrails | Injection success raw → after guardrail | Cost / case | p50 / p95 latency | Gates |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `gemini-3.5-flash-lite` | 95.0% | 100.0% | 0 (0) | 0.977 | 95.0% | 80.0% | 97.4% | 0/10 → 0/10 | – | 5.61s / 21.8s | ✅ pass |

**Recommendation:** `gemini-3.5-flash-lite` — the cheapest model that passes every release gate.

### Gate failures

- `gemini-3.5-flash-lite`: none

### Detail

| Model | Event type | Receipt date (absolute / relative) | Dispute date | Date tool used on relative dates | Spurious deadlines | LLM calls / case | Tokens / case | Total cost |
|---|---|---|---|---|---|---|---|---|
| `gemini-3.5-flash-lite` | 97.5% | 100.0% / 100.0% | 92.3% | 100.0% | 0 | 3.02 | 5096 | – |

## Ablations (`gemini-3.5-flash-lite`)

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
| LLM resolves dates itself | 100.0% | 100.0% |
| LLM calls `shift_days` / `find_weekday` | 100.0% | – |

## Personal data

- Blind first run of the pseudonymiser on the frozen test split: **2/159** PII strings leaked (1.3%) — both were employer names without a legal-form suffix.
- After adding a trade-name rule (informed by that run, so no longer blind): 0 leaks; CI keeps this at 0.
- End-to-end, measured on every payload actually sent to a model: see `pii_leak_rate` per model above (gemini-3.5-flash-lite: 0.0%).
