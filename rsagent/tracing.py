"""Minimal LLM observability: per-case trace of node timings, model calls, tokens, cost and every
outbound payload (so PII leakage can be measured on what actually left, not on what we intended)."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Tracer:
    case_id: str
    events: list[dict] = field(default_factory=list)
    payloads: list[str] = field(default_factory=list)
    _t0: float = field(default_factory=time.perf_counter)

    def event(self, name: str, **attrs) -> None:
        self.events.append({"t": round(time.perf_counter() - self._t0, 4), "event": name, **attrs})

    def record_payload(self, payload: str) -> None:
        self.payloads.append(payload)

    class _Span:
        def __init__(self, tracer: "Tracer", name: str):
            self.tracer, self.name = tracer, name

        def __enter__(self):
            self.start = time.perf_counter()
            return self

        def __exit__(self, exc_type, exc, tb):
            self.tracer.event(f"node:{self.name}", duration_s=round(time.perf_counter() - self.start, 4),
                              error=None if exc is None else type(exc).__name__)
            return False

    def span(self, name: str) -> "_Span":
        return Tracer._Span(self, name)

    def summary(self) -> dict:
        llm = [e for e in self.events if "model" in e]
        cost = [e["cost_usd"] for e in llm if e.get("cost_usd") is not None]
        return {
            "llm_calls": len(llm),
            "prompt_tokens": sum(e.get("prompt_tokens") or 0 for e in llm),
            "completion_tokens": sum(e.get("completion_tokens") or 0 for e in llm),
            "llm_latency_s": round(sum(e.get("latency_s") or 0 for e in llm), 3),
            "cost_usd": round(sum(cost), 6) if cost else None,
        }

    def dump(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"case_id": self.case_id, "summary": self.summary(), "events": self.events},
                                   ensure_ascii=False, indent=1, default=str), encoding="utf-8")
