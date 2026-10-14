"""Measure pseudonymisation quality on a golden split (deterministic, no LLM)."""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path
import yaml
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rsagent.pii import pseudonymise

def leaked(value: str, masked: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(value)}(?![\w])", masked, re.IGNORECASE) is not None

def run(split: str, use_ner: bool = True, verbose: bool = False) -> dict:
    cases = yaml.safe_load(Path(f"data/golden/{split}.yaml").read_text(encoding="utf-8"))["cases"]
    total = leaks = 0; cases_with_leak = []; spans_total = spans_fp = 0; leak_list = []; fp_list = []
    for c in cases:
        p = pseudonymise(c["email"], use_ner=use_ner)
        gold = c["gold"]["pii"]
        case_leaks = [v for v in gold if leaked(v, p.text)]
        total += len(gold); leaks += len(case_leaks)
        if case_leaks: cases_with_leak.append(c["id"]); leak_list += [(c["id"], v) for v in case_leaks]
        for s in p.spans:
            spans_total += 1
            if not any(g.lower() in s.text.lower() or s.text.lower() in g.lower() for g in gold):
                spans_fp += 1; fp_list.append((c["id"], s.label, s.text))
        if verbose: print(c["id"], p.text.replace("\n", " | ")[:600], "\n")
    return {"split": split, "use_ner": use_ner, "pii_strings": total, "leaked": leaks,
            "leak_rate": round(leaks / total, 4), "cases_with_leak": cases_with_leak,
            "masked_spans": spans_total, "over_masked_spans": spans_fp,
            "masking_precision": round(1 - spans_fp / max(spans_total, 1), 4),
            "leaks": leak_list, "over_masked": fp_list}

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--split", default="dev"); ap.add_argument("--no-ner", action="store_true")
    ap.add_argument("-v", action="store_true"); ap.add_argument("--out")
    a = ap.parse_args(); r = run(a.split, not a.no_ner, a.v)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    if a.out: Path(a.out).write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
