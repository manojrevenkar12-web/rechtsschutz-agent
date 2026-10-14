"""LLM provider layer.

One OpenAI-compatible client covers OpenRouter (many vendors behind one API, real per-call cost in
the response), GitHub Models and any other compatible endpoint. Every call goes through:

  egress guard  ->  cassette (record / replay)  ->  HTTP  ->  usage + cost accounting  ->  trace

The cassette makes evaluation reproducible: a run with an API key records every response; CI
replays those recordings without a key, so the full graph and all gates run on real model output.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

from .pii import EgressBlocked, egress_check
from .tracing import Tracer

ENDPOINTS = {
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY"),
    "github": ("https://models.github.ai/inference/chat/completions", "GITHUB_TOKEN"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "GEMINI_API_KEY"),
}


class ReplayMiss(RuntimeError):
    pass


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[dict]
    usage: dict
    cost_usd: float | None
    latency_s: float
    model: str
    cached: bool = False
    raw: dict = field(default_factory=dict)


def _key(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32]


class ChatClient:
    """mode: 'live' (call the API), 'record' (call + save), 'replay' (only use saved responses)."""

    def __init__(
        self,
        provider: str = "openrouter",
        mode: str = "record",
        cassette_dir: str | Path = "cassettes",
        max_concurrency: int = 4,
        prices: dict[str, tuple[float, float]] | None = None,
        timeout_s: float = 120.0,
    ):
        self.provider = provider
        self.url, key_env = ENDPOINTS[provider]
        self.url = os.environ.get("RSAGENT_BASE_URL", self.url)
        self.api_key = os.environ.get(key_env, "")
        self.mode = mode
        self.cassette_dir = Path(cassette_dir)
        self.sem = asyncio.Semaphore(max_concurrency)
        self.prices = prices or {}
        self.timeout_s = timeout_s
        self.max_retries = int(os.environ.get("RSAGENT_MAX_RETRIES", "8"))
        if mode in ("live", "record") and not self.api_key:
            raise RuntimeError(f"{key_env} is not set; use mode='replay' to run from recorded responses")

    def _cassette(self, key: str) -> Path:
        return self.cassette_dir / key[:2] / f"{key}.json"

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: Any = None,
        temperature: float = 0.0,
        max_tokens: int = 1500,
        vault_values: list[str] | None = None,
        tracer: Tracer | None = None,
        span_name: str = "llm",
    ) -> LLMResponse:
        outbound = json.dumps(messages, ensure_ascii=False)
        hits = egress_check(outbound, vault_values or [])
        if hits:
            if tracer:
                tracer.event("egress_blocked", reasons=hits)
            raise EgressBlocked("; ".join(hits))
        if tracer:
            tracer.record_payload(outbound)

        payload: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        if self.provider == "gemini":
            # Gemini 2.5 models think by default and the thinking tokens count against max_tokens;
            # keep it small so answers are not truncated and latency stays comparable.
            payload["reasoning_effort"] = "low"
        if tools:
            payload["tools"] = tools
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice
        key = _key({"provider": self.provider, **payload})
        path = self._cassette(key)

        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            resp = self._parse(data["response"], model, data.get("latency_s", 0.0), cached=True)
        elif self.mode == "replay":
            raise ReplayMiss(f"no recording for {model} / {span_name} (key {key})")
        else:
            body = dict(payload)
            if self.provider == "openrouter":
                body["usage"] = {"include": True}
            async with self.sem:
                data, latency = await self._post(body)
            path.parent.mkdir(parents=True, exist_ok=True)
            if self.mode == "record":
                path.write_text(json.dumps({"request_key": key, "model": model, "latency_s": latency, "response": data}, ensure_ascii=False, indent=1), encoding="utf-8")
            resp = self._parse(data, model, latency, cached=False)

        if tracer:
            tracer.event(
                span_name, model=model, latency_s=round(resp.latency_s, 3), prompt_tokens=resp.usage.get("prompt_tokens"),
                completion_tokens=resp.usage.get("completion_tokens"), cost_usd=resp.cost_usd, tool_calls=[t["name"] for t in resp.tool_calls],
            )
        return resp

    async def _post(self, body: dict) -> tuple[dict, float]:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", "Accept": "application/json",
                   "X-GitHub-Api-Version": "2022-11-28", "HTTP-Referer": "https://github.com/manojrevenkar12-web/rechtsschutz-agent", "X-Title": "rechtsschutz-agent"}
        delay = 2.0
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            for attempt in range(9):
                t0 = time.perf_counter()
                r = await client.post(self.url, headers=headers, json=body)
                latency = time.perf_counter() - t0
                if r.status_code == 429 and re.search(r"PerDay|limit: 0|daily", r.text):
                    raise RuntimeError(f"HTTP 429 daily quota exhausted: {r.text[:300]}")
                if r.status_code in (402, 429, 500, 502, 503, 504) and attempt < self.max_retries:  # 402: OpenRouter credit reservation for in-flight calls
                    await asyncio.sleep(min(float(r.headers.get("retry-after", delay)), 65.0))
                    delay = min(delay * 2, 65.0)
                    continue
                if r.status_code >= 400:
                    raise RuntimeError(f"HTTP {r.status_code} from {self.url}: {r.text[:300]}")
                try:
                    data = r.json()
                except json.JSONDecodeError:
                    raise RuntimeError(f"non-JSON response (HTTP {r.status_code}, {r.headers.get('content-type')}): {r.text[:300]!r}")
                if "error" in data and not data.get("choices"):
                    raise RuntimeError(f"provider error: {data['error']}")
                return data, latency
        raise RuntimeError("unreachable")

    def _parse(self, data: dict, model: str, latency: float, cached: bool) -> LLMResponse:
        msg = (data.get("choices") or [{}])[0].get("message", {}) or {}
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            calls.append({"id": tc.get("id") or fn.get("name"), "name": fn.get("name"), "arguments": fn.get("arguments") or "{}",
                          # Gemini 3 attaches a thought signature that must be sent back with the call
                          "extra_content": tc.get("extra_content")})
        usage = data.get("usage") or {}
        cost = usage.get("cost")
        if cost is None and model in self.prices:
            pin, pout = self.prices[model]
            cost = (usage.get("prompt_tokens", 0) * pin + usage.get("completion_tokens", 0) * pout) / 1e6
        return LLMResponse(msg.get("content"), calls, usage, cost, latency, model, cached, data)

    async def stream_text(self, *, model: str, messages: list[dict], vault_values: list[str] | None = None,
                          temperature: float = 0.0, max_tokens: int = 1200) -> AsyncIterator[str]:
        """Token stream for the UI (SSE). Not recorded; evaluation uses `chat`."""
        hits = egress_check(json.dumps(messages, ensure_ascii=False), vault_values or [])
        if hits:
            raise EgressBlocked("; ".join(hits))
        body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens, "stream": True}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            async with client.stream("POST", self.url, headers=headers, json=body) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.startswith("data: ") or line.endswith("[DONE]"):
                        continue
                    try:
                        delta = json.loads(line[6:])["choices"][0]["delta"].get("content")
                    except (KeyError, IndexError, json.JSONDecodeError):
                        continue
                    if delta:
                        yield delta


class ScriptedClient:
    """Deterministic stand-in used by unit tests and the offline demo: returns scripted responses."""

    def __init__(self, script):
        self.script = script  # callable(span_name, messages, tools) -> LLMResponse
        self.calls: list[dict] = []

    async def chat(self, *, model, messages, tools=None, tool_choice=None, vault_values=None, tracer=None, span_name="llm", **_):
        outbound = json.dumps(messages, ensure_ascii=False)
        hits = egress_check(outbound, vault_values or [])
        if hits:
            if tracer:
                tracer.event("egress_blocked", reasons=hits)
            raise EgressBlocked("; ".join(hits))
        if tracer:
            tracer.record_payload(outbound)
        self.calls.append({"span": span_name, "model": model, "messages": messages})
        resp = self.script(span_name, messages, tools)
        if tracer:
            tracer.event(span_name, model=model, latency_s=0.0, prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
                         tool_calls=[t["name"] for t in resp.tool_calls])
        return resp

    async def stream_text(self, *, model, messages, vault_values=None, **_):
        resp = self.script("draft", messages, None)
        for word in (resp.content or "").split(" "):
            yield word + " "
