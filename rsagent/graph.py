"""LangGraph workflow: intake email -> reviewed coverage request.

    pseudonymise -> screen -> extract (tool loop) -> assess (deadline + coverage, deterministic)
        -> draft -> check_draft --(violations, 1 retry)--> draft
                              \\-> human_review (interrupt) -> finalize (re-identify)

LLM calls happen in exactly two nodes (extract, draft). Everything that decides money or
deadlines is deterministic and unit-tested.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Optional, TypedDict

import yaml
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import ValidationError

from . import deadlines as dl
from .coverage import FLAG_TEXT, Policy, coverage_flags, load_policies, route_queue
from .guardrails import CANARY, check_draft
from .injection import screen, spotlight, spotlight_instruction
from .pii import EgressBlocked, Pseudonymised, pseudonymise
from .schemas import DATE_TOOLS, CaseFacts, tool_schema
from .tracing import Tracer

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@dataclass
class Settings:
    extraction_model: str = "openai/gpt-4.1-mini"
    drafting_model: str = "openai/gpt-4.1-mini"
    prompt_version: str = "v1"
    spotlighting: bool = True
    date_tools: bool = True
    max_tool_rounds: int = 5
    max_draft_attempts: int = 2
    court_subdiv: str = "BE"


class CaseState(TypedDict, total=False):
    case_id: str
    received_at: str
    policy_id: str
    email: str
    masked_email: str
    vault_values: list[str]
    originals: dict[str, str]
    injection_hits: list[str]
    facts: Optional[dict]
    extraction: dict
    deadline: Optional[dict]
    flags: list[str]
    review_flags: list[str]
    queue: str
    draft: Optional[str]
    raw_drafts: list[str]
    draft_violations: list[str]
    draft_attempts: int
    status: str
    decision: Optional[dict]
    final_letter: Optional[str]
    error: Optional[str]


def load_prompts(version: str) -> dict:
    return yaml.safe_load((PROMPTS_DIR / f"{version}.yaml").read_text(encoding="utf-8"))


def _seeded_spotlight(case_id: str, text: str) -> tuple[str, str]:
    # Deterministic boundary per case so recorded responses can be replayed exactly.
    tag = "client_email_" + hashlib.sha256(case_id.encode()).hexdigest()[:8]
    safe = text.replace(f"</{tag}>", "")
    return f"<{tag}>\n{safe}\n</{tag}>", tag


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower().strip("\"'„“”.,")


def _run_date_tool(name: str, args: dict) -> str:
    try:
        base = date.fromisoformat(args["base_date"])
        if name == "shift_days":
            d = dl.shift_days(base, int(args["days"]))
        elif name == "find_weekday":
            which = args.get("which", "most_recent")
            if which == "most_recent":
                d = dl.most_recent_weekday(base, args["weekday"])
            elif which == "previous_week":
                d = dl.weekday_in_week(base, args["weekday"], -1)
            else:
                d = dl.weekday_in_week(base, args["weekday"], 0)
        else:
            return json.dumps({"error": f"unknown tool {name}"})
        return json.dumps({"date": d.isoformat(), "weekday": dl.WEEKDAYS_DE[d.weekday()]})
    except Exception as e:  # tool errors go back to the model, they never crash the graph
        return json.dumps({"error": str(e)})


def build_graph(client, settings: Settings | None = None, policies: dict[str, Policy] | None = None, checkpointer=None):
    settings = settings or Settings()
    policies = policies or load_policies()
    prompts = load_prompts(settings.prompt_version)

    def tracer_of(config) -> Tracer:
        return config["configurable"]["tracer"]

    def email_block(state) -> tuple[str, str]:
        if settings.spotlighting:
            block, tag = _seeded_spotlight(state["case_id"], state["masked_email"])
            return block, spotlight_instruction(tag)
        return state["masked_email"], ""

    # ------------------------------------------------------------------ nodes
    def n_pseudonymise(state: CaseState, config) -> CaseState:
        with tracer_of(config).span("pseudonymise"):
            p: Pseudonymised = pseudonymise(state["email"])
            originals = {ph: p._original_case.get(ph, v) for ph, v in p.vault.originals.items()}
            return {"masked_email": p.text, "vault_values": p.vault.values(), "originals": originals,
                    "review_flags": [], "raw_drafts": [], "draft_attempts": 0}

    def n_screen(state: CaseState, config) -> CaseState:
        with tracer_of(config).span("screen"):
            hits = screen(state["masked_email"])
            rf = list(state.get("review_flags", []))
            if hits:
                rf.append("manipulationsversuch")
            return {"injection_hits": hits, "review_flags": rf}

    async def n_extract(state: CaseState, config) -> CaseState:
        tr = tracer_of(config)
        with tr.span("extract"):
            received = date.fromisoformat(state["received_at"])
            block, spot = email_block(state)
            system = prompts["extraction_system"].format(
                canary=CANARY, spotlight=spot, received_at=received.isoformat(),
                received_weekday=dl.WEEKDAYS_DE[received.weekday()])
            if not settings.date_tools:
                system = system.replace("call the date tools to resolve it. Do not do date arithmetic in your head.",
                                        "resolve it yourself.")
            messages: list[dict] = [{"role": "system", "content": system},
                                    {"role": "user", "content": prompts["extraction_user"].format(received_at=received.isoformat(), email_block=block)}]
            tools = ([*DATE_TOOLS] if settings.date_tools else []) + [tool_schema()]
            info = {"rounds": 0, "tool_calls": [], "schema_errors": 0, "valid_first_try": None}
            facts = None
            try:
                for rnd in range(settings.max_tool_rounds):
                    info["rounds"] = rnd + 1
                    resp = await client.chat(model=settings.extraction_model, messages=messages, tools=tools,
                                             tool_choice="auto", vault_values=state["vault_values"], tracer=tr, span_name="llm:extract")
                    if not resp.tool_calls:
                        messages += [{"role": "assistant", "content": resp.content or ""},
                                     {"role": "user", "content": "Bitte rufe jetzt submit_case_facts mit allen Feldern auf."}]
                        continue
                    calls_out = []
                    for tc in resp.tool_calls:
                        call = {"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                        if tc.get("extra_content"):
                            call["extra_content"] = tc["extra_content"]
                        calls_out.append(call)
                    messages.append({"role": "assistant", "content": resp.content or None, "tool_calls": calls_out})
                    for tc in resp.tool_calls:
                        info["tool_calls"].append(tc["name"])
                        if tc["name"] == "submit_case_facts":
                            try:
                                facts = CaseFacts.model_validate_json(tc["arguments"])
                                if info["valid_first_try"] is None:
                                    info["valid_first_try"] = True
                                result = json.dumps({"status": "ok"})
                            except (ValidationError, ValueError) as e:
                                info["schema_errors"] += 1
                                if info["valid_first_try"] is None:
                                    info["valid_first_try"] = False
                                result = json.dumps({"status": "invalid", "errors": str(e)[:800]})
                        else:
                            result = _run_date_tool(tc["name"], json.loads(tc["arguments"] or "{}"))
                        messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
                    if facts is not None:
                        break
            except EgressBlocked as e:
                return {"facts": None, "extraction": info, "status": "blocked_egress", "error": str(e),
                        "review_flags": [*state.get("review_flags", []), "egress_blockiert"]}
            if facts is None:
                return {"facts": None, "extraction": info, "status": "extraction_failed",
                        "review_flags": [*state.get("review_flags", []), "extraktion_fehlgeschlagen"]}
            rf = list(state.get("review_flags", []))
            masked = _norm(state["masked_email"])
            for field_, ev in (("event_date", facts.event_date_evidence), ("dispute_start_date", facts.dispute_start_evidence)):
                if getattr(facts, field_) is not None and (not ev or _norm(ev) not in masked):
                    rf.append(f"beleg_fehlt:{field_}")
            if facts.manipulation_attempt and "manipulationsversuch" not in rf:
                rf.append("manipulationsversuch")
            return {"facts": json.loads(facts.model_dump_json()), "extraction": info, "review_flags": rf}

    def n_assess(state: CaseState, config) -> CaseState:
        with tracer_of(config).span("assess"):
            f = state["facts"]
            received = date.fromisoformat(state["received_at"])
            ev = date.fromisoformat(f["event_date"]) if f.get("event_date") else None
            ds = date.fromisoformat(f["dispute_start_date"]) if f.get("dispute_start_date") else None
            deadline = dl.compute_deadline(f["event_type"], ev, settings.court_subdiv) if ev else None
            expected = f["event_type"] != "kein_fristereignis"
            flags = coverage_flags(area=f["area"], event_type=f["event_type"], dispute_start=ds, deadline=deadline,
                                   deadline_expected_but_missing=expected and deadline is None,
                                   received_at=received, policy=policies[state["policy_id"]])
            queue = route_queue(f["area"], flags)
            return {"deadline": deadline.as_dict() if deadline else None, "flags": flags, "queue": queue}

    async def n_draft(state: CaseState, config) -> CaseState:
        tr = tracer_of(config)
        with tr.span("draft"):
            f = state["facts"]
            block, spot = email_block(state)
            d = state.get("deadline")
            deadline_text = (f"{d['action']} bis {date.fromisoformat(d['deadline']):%d.%m.%Y} ({d['legal_basis']})" if d else "keine gesetzliche Frist erkannt")
            placeholders = sorted(set(state["originals"]))
            facts_for_draft = {k: f[k] for k in ("area", "event_type", "event_date", "dispute_start_date", "summary_de", "client_goal_de")}
            messages = [
                {"role": "system", "content": prompts["draft_system"].format(canary=CANARY, spotlight=spot)},
                {"role": "user", "content": prompts["draft_user"].format(
                    facts_json=json.dumps(facts_for_draft, ensure_ascii=False, indent=1), deadline_text=deadline_text,
                    letter_date=date.fromisoformat(state["received_at"]).strftime("%d.%m.%Y"),
                    placeholders=", ".join(placeholders) or "keine", email_block=block)},
            ]
            if state.get("draft_violations") and state.get("draft"):
                messages += [{"role": "assistant", "content": state["draft"]},
                             {"role": "user", "content": prompts["draft_repair"].format(violations="; ".join(state["draft_violations"]))}]
            try:
                resp = await client.chat(model=settings.drafting_model, messages=messages, vault_values=state["vault_values"],
                                         tracer=tr, span_name="llm:draft", max_tokens=4000)
            except EgressBlocked as e:
                return {"draft": None, "status": "blocked_egress", "error": str(e)}
            text = (resp.content or "").strip()
            return {"draft": text, "raw_drafts": [*state.get("raw_drafts", []), text], "draft_attempts": state.get("draft_attempts", 0) + 1}

    def n_check(state: CaseState, config) -> CaseState:
        with tracer_of(config).span("check_draft"):
            f, d = state["facts"], state.get("deadline")
            allowed = {state["received_at"]}
            for k in ("event_date", "dispute_start_date"):
                if f.get(k):
                    allowed.add(f[k])
            if d:
                allowed |= {d["deadline"], d["raw_end"], d["event_date"]}
            for dd, mm, yy in re.findall(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b", state["masked_email"]):
                allowed.add(f"{int(yy):04d}-{int(mm):02d}-{int(dd):02d}")
            violations = check_draft(state.get("draft") or "", allowed_dates=allowed, known_placeholders=set(state["originals"]))
            return {"draft_violations": violations}

    def after_check(state: CaseState) -> str:
        if state.get("draft_violations") and state.get("draft_attempts", 0) < settings.max_draft_attempts:
            return "draft"
        return "human_review"

    def n_human_review(state: CaseState, config) -> CaseState:
        blocked = bool(state.get("draft_violations"))
        decision = interrupt({
            "case_id": state["case_id"], "queue": state["queue"], "flags": [FLAG_TEXT.get(x, x) for x in state["flags"]],
            "review_flags": state.get("review_flags", []), "deadline": state.get("deadline"),
            "draft": None if blocked else state.get("draft"), "draft_blocked": blocked,
            "draft_violations": state.get("draft_violations", []),
        })
        return {"decision": decision}

    def n_finalize(state: CaseState, config) -> CaseState:
        dec = state.get("decision") or {}
        if dec.get("action") != "approve":
            return {"status": "rejected" if dec.get("action") == "reject" else "returned", "final_letter": None}
        text = dec.get("edited_draft") or state.get("draft") or ""
        for ph, original in sorted(state["originals"].items(), key=lambda kv: -len(kv[0])):
            text = text.replace(ph, original)
        return {"status": "approved", "final_letter": text}

    def after_extract(state: CaseState) -> str:
        return "assess" if state.get("facts") else END

    def after_draft(state: CaseState) -> str:
        return "check_draft" if state.get("draft") is not None else END

    g = StateGraph(CaseState)
    g.add_node("pseudonymise", n_pseudonymise)
    g.add_node("screen", n_screen)
    g.add_node("extract", n_extract)
    g.add_node("assess", n_assess)
    g.add_node("draft", n_draft)
    g.add_node("check_draft", n_check)
    g.add_node("human_review", n_human_review)
    g.add_node("finalize", n_finalize)
    g.add_edge(START, "pseudonymise")
    g.add_edge("pseudonymise", "screen")
    g.add_edge("screen", "extract")
    g.add_conditional_edges("extract", after_extract, ["assess", END])
    g.add_edge("assess", "draft")
    g.add_conditional_edges("draft", after_draft, ["check_draft", END])
    g.add_conditional_edges("check_draft", after_check, ["draft", "human_review"])
    g.add_edge("human_review", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver())
