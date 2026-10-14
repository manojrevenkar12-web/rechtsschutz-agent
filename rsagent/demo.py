"""Offline demo client: a rule-based stand-in so the API and graph can be tried without an API key.
It is deliberately simple and is NOT what the evaluation measures (that uses real models)."""
from __future__ import annotations

import json
import re
from datetime import date

from .providers import LLMResponse, ScriptedClient

_TYPES = [
    (r"bußgeldbescheid", "bussgeldbescheid", "verkehrsrecht"),
    (r"vollstreckungsbescheid", "vollstreckungsbescheid", "vertragsrecht"),
    (r"mahnbescheid", "mahnbescheid", "vertragsrecht"),
    (r"mieterhöhung", "mieterhoehungsverlangen", "mietrecht"),
    (r"kündigung", "kuendigung_arbeitsvertrag", "arbeitsrecht"),
    (r"bescheid", "sozialbescheid", "sozialrecht"),
]


def _facts(text: str) -> dict:
    low = text.lower()
    etype, area = "kein_fristereignis", "sonstiges"
    for rx, t, a in _TYPES:
        if re.search(rx, low):
            etype, area = t, a
            break
    dates = [date(int(y), int(m), int(d)) for d, m, y in re.findall(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b", text)]
    ev = max(dates).isoformat() if dates and etype != "kein_fristereignis" else None
    return {"area": area, "area_confidence": "niedrig", "event_type": etype, "event_date": ev, "event_date_evidence": None,
            "dispute_start_date": min(dates).isoformat() if dates else None, "dispute_start_evidence": None,
            "summary_de": "Offline-Demo: regelbasierte Zusammenfassung.", "client_goal_de": "Rechtliche Unterstützung.",
            "missing_information_de": ["Unterlagen"], "manipulation_attempt": False}


def offline_client() -> ScriptedClient:
    def script(span, messages, tools):
        if span == "llm:extract":
            user = messages[1]["content"]
            return LLMResponse(None, [{"id": "demo", "name": "submit_case_facts", "arguments": json.dumps(_facts(user))}], {}, 0.0, 0.0, "offline-demo")
        letter = ("Betreff: Deckungsanfrage\n\nSehr geehrte Damen und Herren,\n\nwir vertreten Ihren Versicherungsnehmer und "
                  "bitten um Erteilung der Deckungszusage für die außergerichtliche und gerichtliche Interessenwahrnehmung. "
                  "Den Sachverhalt fassen wir nach Eingang der Unterlagen zusammen; bitte teilen Sie uns zeitnah mit, ob "
                  "weitere Angaben benötigt werden. Dies ist ein Entwurf der Offline-Demo ohne Sprachmodell und dient nur "
                  "dazu, den Ablauf mit Freigabe durch eine Anwältin oder einen Anwalt zu zeigen.\n\nMit freundlichen Grüßen")
        return LLMResponse(letter, [], {}, 0.0, 0.0, "offline-demo")
    return ScriptedClient(script)
