"""Scripted model that answers from the gold labels — exercises the graph without an API key."""
from __future__ import annotations
import json
from rsagent.providers import LLMResponse, ScriptedClient

DRAFT = ("Betreff: Deckungsanfrage für unseren Mandanten [PERSON_1]\n\nSehr geehrte Damen und Herren,\n\n"
         "wir zeigen an, dass wir die Interessen Ihres Versicherungsnehmers [PERSON_1] vertreten. "
         "Unser Mandant wendet sich gegen eine Maßnahme der Gegenseite und bittet um rechtliche Unterstützung. "
         "Wir bitten Sie, uns die Deckungszusage für die außergerichtliche und gegebenenfalls gerichtliche "
         "Interessenwahrnehmung zu erteilen. Den Sachverhalt haben wir anhand der Angaben des Mandanten "
         "zusammengefasst; weitere Unterlagen reichen wir gern nach.{deadline}\n\n"
         "Für eine kurzfristige Rückmeldung wären wir dankbar.\n\nMit freundlichen Grüßen\nIhre Partnerkanzlei")

def gold_script(case: dict, draft_override: str | None = None, relative_via_tool: bool = True):
    g = case["gold"]
    state = {"tool_used": False}
    def script(span, messages, tools):
        if span == "llm:extract":
            if relative_via_tool and g["date_is_relative"] and g["event_date"] and not state["tool_used"]:
                state["tool_used"] = True
                args = {"base_date": str(case["received_at"]), "days": (g["event_date"] - case["received_at"]).days}
                return LLMResponse(None, [{"id": "c1", "name": "shift_days", "arguments": json.dumps(args)}], {}, 0.0, 0.0, "scripted")
            facts = {"area": g["area"], "area_confidence": "hoch", "event_type": g["event_type"],
                     "event_date": str(g["event_date"]) if g["event_date"] else None, "event_date_evidence": None,
                     "dispute_start_date": str(g["dispute_start_date"]) if g["dispute_start_date"] else None,
                     "dispute_start_evidence": None, "summary_de": "Zusammenfassung.", "client_goal_de": "Hilfe.",
                     "missing_information_de": [], "manipulation_attempt": case["attack"] is not None}
            return LLMResponse(None, [{"id": "c2", "name": "submit_case_facts", "arguments": json.dumps(facts)}], {}, 0.0, 0.0, "scripted")
        if span == "llm:draft":
            if draft_override is not None:
                return LLMResponse(draft_override, [], {}, 0.0, 0.0, "scripted")
            return LLMResponse(DRAFT.format(deadline=""), [], {}, 0.0, 0.0, "scripted")
        raise AssertionError(span)
    return ScriptedClient(script)
