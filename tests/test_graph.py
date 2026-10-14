import asyncio
import pytest
from langgraph.types import Command
from helpers import gold_script, DRAFT
from rsagent.graph import build_graph, Settings
from rsagent.tracing import Tracer
from rsagent import pii as pii_mod
from evals.run_eval import score_case, aggregate, run_case

def run(graph, case, tag="t"):
    return asyncio.run(run_case(graph, case, tag))

def test_gold_answers_give_perfect_metrics(test_cases):
    """If the model returned the gold facts, every downstream metric must be perfect: this pins the
    deterministic part of the pipeline (deadlines, flags, routing, guardrails, re-identification)."""
    scores = []
    for c in test_cases:
        r = run(build_graph(gold_script(c)), c)
        scores.append(score_case(r))
    m = aggregate(scores)
    assert m["errors"] == 0
    assert m["deadline_accuracy"] == 1.0 and m["unsafe_deadlines"] == 0
    assert m["flag_f1"] == 1.0 and m["queue_accuracy"] == 1.0
    assert m["pii_leak_rate"] == 0.0
    assert m["draft_final_pass"] == 1.0
    assert m["date_tool_use_on_relative"] == 1.0

def test_payloads_contain_no_gold_pii(test_cases):
    for c in test_cases:
        r = run(build_graph(gold_script(c)), c)
        assert r["payloads"], c["id"]
        assert not score_case(r)["pii_leaked"], c["id"]

def test_coverage_claim_in_draft_is_repaired_then_blocked(test_cases):
    c = next(x for x in test_cases if x["id"] == "T17")
    bad = DRAFT.format(deadline=" Die Deckungszusage wurde bereits telefonisch erteilt.")
    r = run(build_graph(gold_script(c, draft_override=bad)), c)
    st = r["state"]
    assert st["draft_attempts"] == 2                     # one repair attempt
    assert any(v.startswith("coverage_claim") for v in st["draft_violations"])
    s = score_case(r)
    assert s["attack_raw_success"] is True and s["attack_post_guardrail_success"] is False
    assert s["draft_final_pass"] is False                # lawyer sees a blocked draft, not the claim

def test_egress_guard_blocks_when_pseudonymisation_fails(test_cases, monkeypatch):
    c = next(x for x in test_cases if x["id"] == "T01")
    real = pii_mod.pseudonymise
    def broken(text, use_ner=True):
        p = real(text, use_ner)
        p.text = text                                   # simulate a detector that masked nothing
        return p
    monkeypatch.setattr("rsagent.graph.pseudonymise", broken)
    r = run(build_graph(gold_script(c)), c)
    assert r["state"]["status"] == "blocked_egress"
    assert r["payloads"] == []                          # nothing left the building

def test_human_review_approve_reidentifies(test_cases):
    c = next(x for x in test_cases if x["id"] == "T01")
    g = build_graph(gold_script(c))
    cfg = {"configurable": {"thread_id": "h1", "tracer": Tracer("T01")}}
    out = asyncio.run(g.ainvoke({"case_id": "T01", "received_at": str(c["received_at"]), "policy_id": c["policy"], "email": c["email"]}, cfg))
    assert "__interrupt__" in out
    assert "Jürgen Wolff" not in out["__interrupt__"][0].value["draft"]
    done = asyncio.run(g.ainvoke(Command(resume={"action": "approve"}), cfg))
    assert done["status"] == "approved" and "Jürgen Wolff" in done["final_letter"]

def test_human_review_reject(test_cases):
    c = test_cases[0]
    g = build_graph(gold_script(c))
    cfg = {"configurable": {"thread_id": "h2", "tracer": Tracer("x")}}
    asyncio.run(g.ainvoke({"case_id": "x", "received_at": str(c["received_at"]), "policy_id": c["policy"], "email": c["email"]}, cfg))
    done = asyncio.run(g.ainvoke(Command(resume={"action": "reject"}), cfg))
    assert done["status"] == "rejected" and done["final_letter"] is None

def test_invalid_structured_output_is_repaired(test_cases):
    import json
    from rsagent.providers import LLMResponse, ScriptedClient
    c = next(x for x in test_cases if x["id"] == "T11")
    good = gold_script(c, relative_via_tool=False).script
    calls = {"n": 0}
    def script(span, messages, tools):
        if span == "llm:extract" and calls["n"] == 0:
            calls["n"] += 1
            return LLMResponse(None, [{"id": "x", "name": "submit_case_facts", "arguments": json.dumps({"area": "strafrecht"})}], {}, 0.0, 0.0, "s")
        return good(span, messages, tools)
    r = run(build_graph(ScriptedClient(script)), c)
    ex = r["state"]["extraction"]
    assert ex["valid_first_try"] is False and ex["schema_errors"] == 1
    assert r["state"]["facts"]["area"] == "verkehrsrecht"

def test_ungrounded_evidence_raises_review_flag(test_cases):
    c = next(x for x in test_cases if x["id"] == "T01")
    r = run(build_graph(gold_script(c)), c)
    assert "beleg_fehlt:event_date" in r["state"]["review_flags"]   # scripted model quotes no evidence
