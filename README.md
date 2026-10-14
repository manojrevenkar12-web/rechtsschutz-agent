# rechtsschutz-agent

**An LLM intake agent for German legal-protection insurance (Rechtsschutz) claims — built around the
parts that must not go wrong: client secrecy, statutory deadlines and prompt injection.**

A client writes an email ("Ich habe gestern die Kündigung bekommen …"). The agent pseudonymises it,
works out the legal area and which deadline-triggering document arrived and when, computes the
deadline with the actual statutory rules, flags coverage questions against the insurer's policy
record, drafts the coverage request (Deckungsanfrage) to the insurer — and stops for a lawyer to
approve before any personal data is put back.

<!-- RESULTS:START -->
**Measured on a frozen 40-case test split, Gemini 3.5 Flash-Lite, prompts as first written** ([full report](docs/RESULTS.md)):

| | |
|---|---|
| Statutory deadlines computed correctly | **25/25** — none later than the true deadline |
| Legal area / work queue correct | 95% / 95% |
| Coverage-flag F1 (waiting period, prior dispute, uninsured module …) | 0.98 |
| Personal data reaching the model | **0 of 159** PII strings (measured on every outbound payload) |
| Embedded prompt-injection attacks that succeeded | **0/10** (all 10 also flagged for the lawyer) |
| Letters passing the guardrails | 38/39 — the guardrail caught invented placeholders in 17 first drafts |
| Release gates (8) | all pass; CI re-checks them on every push from the recorded responses |

The report also lists what went wrong on the way (truncated letters in the first run, one extraction failure)
and why the ablations are too small to show an effect.
<!-- RESULTS:END -->

```
 client email
     │
 ┌───▼──────────┐   PII → [PERSON_1], [IBAN_1] …  (vault stays on our side)
 │ pseudonymise │
 └───┬──────────┘
 ┌───▼──────────┐   heuristic injection screen → review flag, never a silent block
 │ screen       │
 └───┬──────────┘
 ┌───▼──────────┐   LLM, tool calling: shift_days / find_weekday for relative dates,
 │ extract      │   submit_case_facts = Pydantic-validated structured output (1 repair loop),
 └───┬──────────┘   evidence quotes must be found verbatim in the email
 ┌───▼──────────┐   deterministic: §§ 187/188/193 BGB, § 4 KSchG, § 67 OWiG, § 692 ZPO, § 84 SGG,
 │ assess       │   § 558b BGB + Berlin holidays; waiting period, prior dispute, uninsured module;
 └───┬──────────┘   routing to a work queue
 ┌───▼──────────┐   LLM drafts the Deckungsanfrage with placeholders only
 │ draft        │◄──┐
 └───┬──────────┘   │ 1 repair attempt
 ┌───▼──────────┐   │ guardrails: no "coverage already granted", no prompt leak,
 │ check_draft  │───┘ only dates that occur in the facts, only known placeholders
 └───┬──────────┘
 ┌───▼──────────┐   LangGraph interrupt: approve / edit / reject
 │ human_review │
 └───┬──────────┘
 ┌───▼──────────┐   placeholders → real names, only after approval
 │ finalize     │
 └──────────────┘
 every outbound LLM payload passes the egress guard (blocks if any vault value is present) and is traced
```

## Why it is built this way

| Concern | Decision |
|---|---|
| **§ 203 StGB / GDPR** — lawyer–client secrecy | Personal data is replaced before any model call; the mapping never leaves the process. An **egress guard** scans every outbound payload for vault values and raw identifiers and **fails closed**. Leakage is measured on what was actually sent, not on what the masking intended. |
| **Missed deadlines are the costliest error** | The LLM only extracts *which* document arrived and *when*. Date arithmetic is done by tools (relative dates) and a deterministic engine (statutory periods, weekend/holiday shift). The key safety metric is a deadline **later than the true one without any warning flag**. |
| **Prompt injection** in client emails | Spotlighting (randomised data boundary), coverage/deadline decisions kept out of the model, draft guardrails, and a review flag. An **ablation** measures what spotlighting alone buys. |
| **Coverage is the insurer's decision** | The system never decides coverage; it raises flags (waiting period, prior dispute, module not insured, objection procedure not covered) for the lawyer. |
| **Model choice, cost** | Same pipeline run across several models; the report picks the cheapest model that passes every release gate. |
| **Reproducibility** | Every model response is recorded (`cassettes/`). CI replays them without an API key and re-checks all gates on every push. |

## The data

`data/golden/` holds 60 hand-written German client emails (40 test, 20 dev) with gold labels for
legal area, triggering document, receipt date, dispute start, deadline, coverage flags, every PII
string, and — in 14 of them — an embedded prompt-injection attempt (area override, date override,
fake "coverage already granted", system-prompt exfiltration; German and English).

* All people, companies, numbers and addresses are fictitious.
* The test split was committed **before** any detector, prompt or rule was written
  (see the first commit). Prompts and the pseudonymiser were tuned on dev only.
* All 60 hand-computed deadlines and flags are asserted against the engine in the unit tests,
  so the dataset and the rules check each other.
* Policy records (`data/policies.yaml`) are illustrative ARB-style tariffs; real tariffs differ.

**Limits, stated plainly:** 40 test cases is small — treat accuracy differences of a few points
between models as noise. The emails are synthetic and written by one author, so they are cleaner than
real intake mail. The injection screen was written by the same author as the attacks, so its 14/14
detection is not a blind result; the measured defences are spotlighting and the architecture.

## Run it

```bash
pip install -r requirements-dev.txt
pytest -q                                              # 44 tests, no API key needed

# evaluation from the recorded responses (no key)
python evals/run_eval.py --split test --mode replay --gate
python evals/report.py --split test                    # -> docs/RESULTS.md

# live evaluation (records new responses)
export OPENROUTER_API_KEY=...
python evals/run_eval.py --split test --mode record

# API with SSE streaming; RSAGENT_OFFLINE=1 runs a rule-based stand-in without a key
RSAGENT_OFFLINE=1 uvicorn rsagent.api:app
curl -N -X POST localhost:8000/cases -H 'content-type: application/json' \
  -d '{"policy_id":"POL-A","received_at":"2026-09-21","email":"Am 18.09.2026 hat mir mein Arbeitgeber gekündigt. ... Jürgen Wolff"}'
curl -X POST localhost:8000/cases/<id>/review -H 'content-type: application/json' -d '{"action":"approve","reviewer":"RAin Muster"}'
```

## Layout

```
rsagent/
  deadlines.py   statutory deadline engine + calendar tools        coverage.py  policy rules, flags, routing
  pii.py         pseudonymisation, vault, egress guard             injection.py screen + spotlighting
  schemas.py     structured-output contract (submit_case_facts)    guardrails.py draft checks, attack scoring
  providers.py   OpenAI-compatible client, record/replay, cost     tracing.py   per-case trace + payload log
  graph.py       LangGraph workflow with human-in-the-loop          api.py       FastAPI + SSE
evals/           run_eval.py (metrics, gates, ablations), report.py, pii_eval.py
data/golden/     test.yaml (frozen) and dev.yaml                   prompts/v1.yaml
```

*Not legal advice. A triage and drafting aid; every output is reviewed by a lawyer.*
