# Evaluation results — `test` split (40 cases)

Provider: `gemini` · prompt version `v1` · temperature 0 · every number below is recomputed by CI from the recorded responses in `cassettes/`.

## Model selection

| Model | Area | Deadline exact | Unsafe deadline (silent) | Flag F1 | Queue | Valid JSON 1st try | Draft passes guardrails | Injection success raw → after guardrail | Cost / case (tokens) | p50 / p95 latency | Gates |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `gemini-3.5-flash-lite` | 95.0% | 100.0% | 0 (0) | 0.977 | 95.0% | 80.0% | 97.4% | 0/10 → 0/10 | – (5096) | 5.61s / 21.8s | ✅ pass |

Only `gemini-3.5-flash-lite` could be evaluated in this run (see *How these numbers came about*); the harness compares any number of models and recommends the cheapest one that passes every gate.

### Gate failures

- `gemini-3.5-flash-lite`: none

### Detail

| Model | Event type | Receipt date (absolute / relative) | Dispute date | Date tool used on relative dates | Spurious deadlines | LLM calls / case | Tokens / case | Total cost |
|---|---|---|---|---|---|---|---|---|
| `gemini-3.5-flash-lite` | 97.5% | 100.0% / 100.0% | 92.3% | 100.0% | 0 | 3.02 | 5096 | – |

## Ablations (`gemini-3.5-flash-lite`, compared on the same cases)

### Prompt-injection defence (the 10 cases with an embedded attack)

| Setting | Attack succeeded (raw model output) | Reached the lawyer unflagged | Case flagged as manipulation |
|---|---|---|---|
| without spotlighting | 0/10 | 0/10 | 10/10 |
| with spotlighting | 0/10 | 0/10 | 10/10 |

### Date tools vs. date arithmetic inside the model (6 cases with relative dates)

| Setting | Receipt date correct | Deadline correct | Dispute start correct | Coverage flags exactly right |
|---|---|---|---|---|
| model resolves dates itself | 5/5 | 5/5 | 2/5 | 5/6 |
| model calls `shift_days` / `find_weekday` | 5/5 | 5/5 | 3/5 | 6/6 |

## Personal data

- Blind first run of the pseudonymiser on the frozen test split: **2/159** PII strings leaked (1.3%) — both were employer names without a legal-form suffix.
- After adding a trade-name rule (informed by that run, so no longer blind): 0 leaks; CI keeps this at 0.
- End-to-end, measured on every payload actually sent to a model: see `pii_leak_rate` per model above (gemini-3.5-flash-lite: 0.0%).

## How these numbers came about

- **One model.** The evaluation ran on Google AI Studio's free tier on 14 Oct 2026. Gemini 3.5 Flash-Lite was the
  only model the free key could call; 3.1 Flash-Lite, 3.5 Flash and 3.7 Flash returned "high demand" (HTTP 503) and
  3.6/3.8 Flash had no free quota. The harness is provider-agnostic (`config/eval*.yaml`) and the comparison table
  fills in as soon as more models are run.
- **No prompt tuning on the test split.** Prompts are `prompts/v1.yaml` as first written. The test split was never
  used to tune prompts or rules; the failures below are reported, not patched.
- **First run: 8 of 39 letters were truncated.** Gemini counts its thinking tokens against `max_tokens`, so letters
  stopped mid-sentence (`finish_reason=length`) and failed the length guardrail. The draft budget was raised from
  1,200 to 4,000 tokens and only drafting was re-run; extraction outputs are the original recordings.
- **The guardrail earned its place.** In 17 of 39 first drafts the model invented placeholders for reference numbers
  the client never gave (e.g. a policy number). The check caught every one; after one repair round 38/39 letters pass
  and the remaining one reaches the lawyer marked as blocked, not as a clean draft.
- **One extraction failure (T14).** The model sent the string `"null"` instead of JSON `null` for a date three times;
  the schema-repair loop did not recover within five rounds, so the case was routed to manual handling. No deadline
  was involved. Coercing `"null"` would fix it, but that would be tuning on the test split.
- **Small sample.** 40 cases (25 with a statutory deadline, 10 with an attack). Read differences of a case or two as
  noise. The ablations in particular are too small to show an effect: this model ignored every embedded attack with or
  without spotlighting, and the date-tool comparison differs by a single dispute date.
