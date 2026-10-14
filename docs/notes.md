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
