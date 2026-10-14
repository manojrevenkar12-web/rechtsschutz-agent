"""HTTP API with server-sent events.

POST /cases                      start a case; streams node events and finishes at the lawyer-review interrupt
POST /cases/{case_id}/review     approve / reject / edit the draft (resumes the graph)
GET  /cases/{case_id}            current state (pseudonymised; originals are never returned before approval)
GET  /healthz

Run:  uvicorn rsagent.api:app --reload   (OPENROUTER_API_KEY set), or RSAGENT_OFFLINE=1 for a scripted demo.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import date
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from langgraph.types import Command
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from .graph import Settings, build_graph
from .tracing import Tracer

app = FastAPI(title="rechtsschutz-agent", version="0.1.0")
_TRACERS: dict[str, Tracer] = {}
_GRAPH = None


def get_graph():
    global _GRAPH
    if _GRAPH is None:
        if os.environ.get("RSAGENT_OFFLINE") == "1":
            from .demo import offline_client
            client = offline_client()
        else:
            from .providers import ChatClient
            client = ChatClient(provider=os.environ.get("RSAGENT_PROVIDER", "openrouter"), mode="live")
        model = os.environ.get("RSAGENT_MODEL", "openai/gpt-4.1-mini")
        _GRAPH = build_graph(client, Settings(extraction_model=model, drafting_model=os.environ.get("RSAGENT_DRAFT_MODEL", model)))
    return _GRAPH


def set_graph(graph) -> None:  # used by tests
    global _GRAPH
    _GRAPH = graph


class NewCase(BaseModel):
    email: str = Field(min_length=20, max_length=20000)
    policy_id: str
    received_at: date = Field(default_factory=date.today)


class Review(BaseModel):
    action: Literal["approve", "reject", "return"]
    edited_draft: Optional[str] = None
    reviewer: str = Field(min_length=1)


def _public(values: dict) -> dict:
    keep = ("case_id", "received_at", "policy_id", "masked_email", "injection_hits", "facts", "deadline", "flags",
            "review_flags", "queue", "draft", "draft_violations", "status", "final_letter")
    return {k: values.get(k) for k in keep if k in values}


def _cfg(case_id: str) -> dict:
    tr = _TRACERS.setdefault(case_id, Tracer(case_id))
    return {"configurable": {"thread_id": case_id, "tracer": tr}}


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/cases")
async def create_case(body: NewCase):
    graph = get_graph()
    case_id = uuid.uuid4().hex[:12]
    inp = {"case_id": case_id, "received_at": body.received_at.isoformat(), "policy_id": body.policy_id, "email": body.email}

    async def events():
        yield {"event": "case", "data": json.dumps({"case_id": case_id})}
        async for chunk in graph.astream(inp, _cfg(case_id), stream_mode="updates"):
            for node, update in chunk.items():
                if node == "__interrupt__":
                    yield {"event": "review_required", "data": json.dumps(update[0].value, ensure_ascii=False, default=str)}
                else:
                    safe = {k: v for k, v in (update or {}).items() if k not in ("vault_values", "originals", "email")}
                    yield {"event": "node", "data": json.dumps({"node": node, "update": safe}, ensure_ascii=False, default=str)}
        yield {"event": "trace", "data": json.dumps(_TRACERS[case_id].summary())}

    return EventSourceResponse(events())


@app.get("/cases/{case_id}")
def get_case(case_id: str):
    snap = get_graph().get_state(_cfg(case_id))
    if not snap.values:
        raise HTTPException(404, "unknown case")
    return {"state": _public(snap.values), "next": list(snap.next), "trace": _TRACERS[case_id].summary()}


@app.post("/cases/{case_id}/review")
async def review(case_id: str, body: Review):
    graph = get_graph()
    snap = graph.get_state(_cfg(case_id))
    if not snap.values:
        raise HTTPException(404, "unknown case")
    if "human_review" not in snap.next:
        raise HTTPException(409, "case is not waiting for review")
    if body.action == "approve" and snap.values.get("draft_violations") and not body.edited_draft:
        raise HTTPException(422, "draft failed guardrails; approve only with an edited draft")
    out = await graph.ainvoke(Command(resume=body.model_dump()), _cfg(case_id))
    return {"status": out.get("status"), "final_letter": out.get("final_letter"), "reviewer": body.reviewer}
