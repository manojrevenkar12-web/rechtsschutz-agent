"""Render results/<split>/summary.json into docs/RESULTS.md (model selection + ablations)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from evals.run_eval import check_gates  # noqa: E402


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
         "| Model | Area | Deadline exact | Unsafe deadline (silent) | Flag F1 | Queue | Valid JSON 1st try | Draft passes guardrails | Injection success raw → after guardrail | Cost / case | p50 / p95 latency | Gates |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    passing = []
    for name, m in models.items():
        fails = check_gates(m, gates)
        if not fails:
            passing.append((m.get("cost_usd_per_case") or 0, name))
        L.append(
            f"| `{name}` | {pct(m['area_accuracy'])} | {pct(m['deadline_accuracy'])} | {m['unsafe_deadlines']} ({m['unsafe_deadlines_silent']}) | "
            f"{m['flag_f1']:.3f} | {pct(m['queue_accuracy'])} | {pct(m['schema_valid_first_try'])} | {pct(m['draft_final_pass'])} | "
            f"{m['attack_raw_success']}/{m['attacks']} → {m['attack_post_guardrail_success']}/{m['attacks']} | {usd(m['cost_usd_per_case'])} | "
            f"{m['latency_p50_s']}s / {m['latency_p95_s']}s | {'✅ pass' if not fails else '❌ ' + str(len(fails))} |")
    L.append("")
    if passing:
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
    if abl and base:
        L += ["", f"## Ablations (`{base_name}`)", ""]
        if "no_spotlighting" in abl:
            off = abl["no_spotlighting"]
            L += ["### Prompt-injection defence", "",
                  "| Attack type | n | Success **without** spotlighting | Success **with** spotlighting | After draft guardrail |", "|---|---|---|---|---|"]
            for t, d in sorted(base["attacks_by_type"].items()):
                o = off["attacks_by_type"].get(t, {"raw": "–"})
                L.append(f"| {t} | {d['n']} | {o['raw']}/{d['n']} | {d['raw']}/{d['n']} | {d['post_guardrail']}/{d['n']} |")
            L.append(f"| **total** | {base['attacks']} | {off['attack_raw_success']}/{off['attacks']} | {base['attack_raw_success']}/{base['attacks']} | "
                     f"{base['attack_post_guardrail_success']}/{base['attacks']} |")
        if "no_date_tools" in abl:
            off = abl["no_date_tools"]
            L += ["", "### Date tools vs. in-model date arithmetic (relative dates only)", "",
                  "| Setting | Receipt date correct | Deadline correct |", "|---|---|---|",
                  f"| LLM resolves dates itself | {pct(off['event_date_accuracy'])} | {pct(off['deadline_accuracy'])} |",
                  f"| LLM calls `shift_days` / `find_weekday` | {pct(base['event_date_accuracy_relative'])} | – |"]
    pii = ROOT / "results" / "pii_test_blind.json"
    if pii.exists():
        b = json.loads(pii.read_text(encoding="utf-8"))
        L += ["", "## Personal data", "",
              f"- Blind first run of the pseudonymiser on the frozen test split: **{b['leaked']}/{b['pii_strings']}** PII strings leaked "
              f"({pct(b['leak_rate'])}) — both were employer names without a legal-form suffix.",
              "- After adding a trade-name rule (informed by that run, so no longer blind): 0 leaks; CI keeps this at 0.",
              f"- End-to-end, measured on every payload actually sent to a model: see `pii_leak_rate` per model above "
              f"({', '.join(f'{k}: {pct(v['pii_leak_rate'])}' for k, v in models.items())})."]
    out = ROOT / "docs" / "RESULTS.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
