"""Coverage pre-check (Deckungsvorprüfung).

Deterministic rules that turn extracted facts + the insurer's policy record into *review flags*.
The system never decides coverage; it tells the lawyer and the insurer what needs checking.
Keeping this out of the LLM is also a prompt-injection control: no text in a client email can
make the system claim that coverage exists.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml
from dateutil.relativedelta import relativedelta

from .deadlines import Deadline

AREAS = ["arbeitsrecht", "verkehrsrecht", "mietrecht", "vertragsrecht", "sozialrecht", "sonstiges"]

FLAG_TEXT = {
    "nicht_versichert": "Rechtsgebiet ist im Vertrag nicht versichert – Deckung manuell prüfen",
    "vorvertraglich": "Streit begann vor Versicherungsbeginn (Vorvertraglichkeit)",
    "wartezeit": "Rechtsschutzfall innerhalb der Wartezeit",
    "rs_datum_fehlt": "Beginn des Rechtsschutzfalls unklar, Vertrag ist jung – Datum erfragen",
    "widerspruch_nicht_gedeckt": "Tarif deckt das sozialrechtliche Widerspruchsverfahren nicht",
    "frist_kritisch": "Frist endet in höchstens 7 Tagen – priorisieren",
    "frist_abgelaufen": "Frist ist bereits abgelaufen – Wiedereinsetzung/nachträgliche Zulassung prüfen",
    "frist_unklar": "Fristauslösendes Dokument erkannt, aber Zugangsdatum unklar – erfragen",
}
CRITICAL_DAYS = 7


@dataclass(frozen=True)
class Policy:
    id: str
    tariff: str
    start: date
    modules: tuple[str, ...]
    sozial_widerspruchsverfahren: bool
    waiting_period_months: dict[str, int] = field(default_factory=dict)

    def waiting_end(self, area: str) -> date:
        """First day on which a Rechtsschutzfall is outside the waiting period."""
        return self.start + relativedelta(months=self.waiting_period_months.get(area, 3))


def load_policies(path: str | Path = Path(__file__).resolve().parent.parent / "data" / "policies.yaml") -> dict[str, Policy]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    wp = raw["defaults"]["waiting_period_months"]
    return {
        pid: Policy(pid, p["tariff"], p["start"], tuple(p["modules"]), p["sozial_widerspruchsverfahren"], wp)
        for pid, p in raw["policies"].items()
    }


def coverage_flags(
    *,
    area: str,
    event_type: str,
    dispute_start: date | None,
    deadline: Deadline | None,
    deadline_expected_but_missing: bool,
    received_at: date,
    policy: Policy,
) -> list[str]:
    flags: list[str] = []
    if area not in policy.modules:
        flags.append("nicht_versichert")
    else:
        if dispute_start is not None:
            if dispute_start < policy.start:
                flags.append("vorvertraglich")
            elif dispute_start < policy.waiting_end(area):
                flags.append("wartezeit")
        elif policy.start + relativedelta(months=12) > received_at:
            flags.append("rs_datum_fehlt")
        if area == "sozialrecht" and event_type == "sozialbescheid" and not policy.sozial_widerspruchsverfahren:
            flags.append("widerspruch_nicht_gedeckt")
    if deadline is not None:
        days_left = (deadline.deadline - received_at).days
        if days_left < 0:
            flags.append("frist_abgelaufen")
        elif days_left <= CRITICAL_DAYS:
            flags.append("frist_kritisch")
    elif deadline_expected_but_missing:
        flags.append("frist_unklar")
    return flags


def route_queue(area: str, flags: list[str]) -> str:
    """Which work queue the case lands in. Deadline risk beats coverage questions beats routine."""
    if {"frist_abgelaufen", "frist_kritisch", "frist_unklar"} & set(flags):
        return "prioritaet_frist"
    if {"nicht_versichert", "vorvertraglich", "wartezeit", "widerspruch_nicht_gedeckt"} & set(flags):
        return "deckungspruefung"
    if area == "sonstiges":
        return "triage_manuell"
    return f"anwalt_{area}"
