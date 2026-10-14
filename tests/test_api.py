import json
from fastapi.testclient import TestClient
from helpers import gold_script
from rsagent import api
from rsagent.graph import build_graph

def _events(text):
    out = []
    for block in text.strip().split("\r\n\r\n"):
        ev = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in ev:
            out.append((ev["event"], json.loads(ev["data"])))
    return out

def test_stream_review_and_approve(test_cases):
    c = next(x for x in test_cases if x["id"] == "T19")
    api.set_graph(build_graph(gold_script(c)))
    client = TestClient(api.app)
    r = client.post("/cases", json={"email": c["email"], "policy_id": c["policy"], "received_at": str(c["received_at"])})
    assert r.status_code == 200
    events = _events(r.text)
    kinds = [k for k, _ in events]
    assert kinds[0] == "case" and "review_required" in kinds and kinds[-1] == "trace"
    nodes = [d["node"] for k, d in events if k == "node"]
    assert nodes[:4] == ["pseudonymise", "screen", "extract", "assess"]
    assert "Hannah Schmitt" not in r.text            # nothing personal is streamed before approval
    case_id = events[0][1]["case_id"]
    review = next(d for k, d in events if k == "review_required")
    assert review["deadline"]["deadline"] == "2026-11-30"
    s = client.get(f"/cases/{case_id}").json()
    assert s["next"] == ["human_review"]
    done = client.post(f"/cases/{case_id}/review", json={"action": "approve", "reviewer": "RAin Test"}).json()
    assert done["status"] == "approved" and "Hannah Schmitt" in done["final_letter"]
    assert client.post(f"/cases/{case_id}/review", json={"action": "approve", "reviewer": "x"}).status_code == 409
