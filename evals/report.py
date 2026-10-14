"""Render results/<split>/summary.json into docs/RESULTS.md (model selection + ablations)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from evals.run_eval import check_gates, slug  # noqa: E402


def pct(x):
    return "–" if x is None else f"{100 * x:.1f}%"


def usd(x, digits=4):
    return "–" if x is None else f"${x:.{digits}f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="test")
    ap.add_argument("--config", default=str(ROOT / "config" / "eval.yaml"))
    a = ap.parse_args()
    summary = json.loads((ROOT / "results" / a.split / "summary.json").read_text(encoding="utf-8"))
    cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    gates = cfg["gates"]
    models = summary["models"]
    L = [f"# Evaluation results — `{a.split}` split ({next(iter(models.values()))['cases'] if models else 0} cases)", "",
         f"Provider: `{summary['provider']}` · prompt version `{summary['prompt_version']}` · temperature 0 · "
         "every number below is recomputed by CI from the recorded responses in `cassettes/`.", "",
         "## Model selection", "",
         "| Model | Area | Deadline exact | Unsafe deadline (silent) | Flag F1 | Queue | Valid JSON 1st try | Draft passes guardrails | Injection success raw → after guardrail | Cost / case (tokens) | p50 / p95 latency | Gates |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    passing = []
    for name, m in models.items():
        fails = check_gates(m, gates)
        if not fails:
            passing.append((m.get("cost_usd_per_case") or 0, name))
        L.append(
            f"| `{name}` | {pct(m['area_accuracy'])} | {pct(m['deadline_accuracy'])} | {m['unsafe_deadlines']} ({m['unsafe_deadlines_silent']}) | "
            f"{m['flag_f1']:.3f} | {pct(m['queue_accuracy'])} | {pct(m['schema_valid_first_try'])} | {pct(m['draft_final_pass'])} | "
            f"{m['attack_raw_success']}/{m['attacks']} → {m['attack_post_guardrail_success']}/{m['attacks']} | {usd(m['cost_usd_per_case']) if m['cost_usd_per_case'] is not None else '–'} ({m['tokens_per_case']}) | "
            f"{m['latency_p50_s']}s / {m['latency_p95_s']}s | {'✅ pass' if not fails else '❌ ' + str(len(fails))} |")
    L.append("")
    if len(models) == 1:
        only = next(iter(models))
        L.append(f"Only `{only}` could be evaluated in this run (see *How these numbers came about*); the harness compares any "
                 "number of models and recommends the cheapest one that passes every gate.")
    elif passing:
        best = sorted(passing)[0][1]
        L.append(f"**Recommendation:** `{best}` — the cheapest model that passes every release gate.")
    else:
        L.append("**Recommendation:** no model passes every gate; see failures below.")
    L += ["", "### Gate failures", ""]
    for name, m in models.items():
        fails = check_gates(m, gates)
        L.append(f"- `{name}`: " + ("none" if not fails else "; ".join(fails)))

    L += ["", "### Detail", "", "| Model | Event type | Receipt date (absolute / relative) | Dispute date | Date tool used on relative dates | Spurious deadlines | LLM calls / case | Tokens / case | Total cost |",
          "|---|---|---|---|---|---|---|---|---|"]
    for name, m in models.items():
        L.append(f"| `{name}` | {pct(m['event_type_accuracy'])} | {pct(m['event_date_accuracy_absolute'])} / {pct(m['event_date_accuracy_relative'])} | "
                 f"{pct(m['dispute_date_accuracy'])} | {pct(m['date_tool_use_on_relative'])} | {m['spurious_deadlines']} | {m['llm_calls_per_case']} | "
                 f"{m['tokens_per_case']} | {usd(m['cost_usd_total'], 3)} |")

    abl = summary.get("ablations", {})
    base_name = cfg["ablation_model"]
    base = models.get(base_name)
    split_dir = ROOT / "results" / a.split

    def load_cases(d):
        f = split_dir / d / "cases.jsonl"
        return {r["id"]: r for r in map(json.loads, f.read_text(encoding="utf-8").splitlines())} if f.exists() else {}

    if abl and base:
        full = load_cases(slug(base_name))
        L += ["", f"## Ablations (`{base_name}`, compared on the same cases)", ""]
        if "no_spotlighting" in abl:
            off = load_cases("ablation-no-spotlighting")
            on = {k: full[k] for k in off if k in full}
            L += ["### Prompt-injection defence (the 10 cases with an embedded attack)", "",
                  "| Setting | Attack succeeded (raw model output) | Reached the lawyer unflagged | Case flagged as manipulation |", "|---|---|---|---|"]
            for label, rows in (("without spotlighting", off), ("with spotlighting", on)):
                L.append(f"| {label} | {sum(bool(r['attack_raw_success']) for r in rows.values())}/{len(rows)} | "
                         f"{sum(bool(r['attack_post_guardrail_success']) for r in rows.values())}/{len(rows)} | "
                         f"{sum('manipulationsversuch' in r['review_flags'] for r in rows.values())}/{len(rows)} |")
        if "no_date_tools" in abl:
            off = load_cases("ablation-no-date-tools")
            on = {k: full[k] for k in off if k in full}
            L += ["", f"### Date tools vs. date arithmetic inside the model ({len(off)} cases with relative dates)", "",
                  "| Setting | Receipt date correct | Deadline correct | Dispute start correct | Coverage flags exactly right |", "|---|---|---|---|---|"]
            for label, rows in (("model resolves dates itself", off), ("model calls `shift_days` / `find_weekday`", on)):
                ev = [r for r in rows.values() if r["has_gold_event"]]
                dl_ = [r for r in rows.values() if r["has_gold_deadline"]]
                ds = [r for r in rows.values() if r["dispute_ok"] is not None]
                L.append(f"| {label} | {sum(r['event_date_ok'] for r in ev)}/{len(ev)} | {sum(r['deadline_ok'] for r in dl_)}/{len(dl_)} | "
                         f"{sum(bool(r['dispute_ok']) for r in ds)}/{len(ds)} | {sum(r['flags_pred'] == r['flags_gold'] for r in rows.values())}/{len(rows)} |")
    pii = ROOT / "results" / "pii_test_blind.json"
    if pii.exists():
        b = json.loads(pii.read_text(encoding="utf-8"))
        L += ["", "## Personal data", "",
              f"- Blind first run of the pseudonymiser on the frozen test split: **{b['leaked']}/{b['pii_strings']}** PII strings leaked "
              f"({pct(b['leak_rate'])}) — both were employer names without a legal-form suffix.",
              "- After adding a trade-name rule (informed by that run, so no longer blind): 0 leaks; CI keeps this at 0.",
              f"- End-to-end, measured on every payload actually sent to a model: see `pii_leak_rate` per model above "
              f"({', '.join(f'{k}: {pct(v['pii_leak_rate'])}' for k, v in models.items())})."]
    notes = ROOT / "docs" / "notes.md"
    if notes.exists():
        L += ["", notes.read_text(encoding="utf-8").strip()]
    out = ROOT / "docs" / "RESULTS.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
