import json
import pytest
from rsagent.pii import pseudonymise, egress_check

def _leaked(value, text):
    import re
    return re.search(rf"(?<![\w]){re.escape(value)}(?![\w])", text, re.IGNORECASE)

def test_no_gold_pii_survives_on_any_case(all_cases):
    for c in all_cases:
        masked = pseudonymise(c["email"]).text
        assert not [v for v in c["gold"]["pii"] if _leaked(v, masked)], c["id"]

def test_dates_needed_for_deadlines_are_kept(all_cases):
    for c in all_cases:
        masked = pseudonymise(c["email"]).text
        ev = c["gold"]["event_date"]
        if ev and f"{ev:%d.%m.%Y}" in c["email"]:
            assert f"{ev:%d.%m.%Y}" in masked, c["id"]

def test_structured_identifiers():
    t = ("Ich bin erreichbar unter +49 30 2287 4410 oder max@example.org. IBAN DE29 1001 0010 0987 6543 21, "
         "Kennzeichen B-KM 4471, Versicherungsnummer RS-4471-0932, geb. 14.03.1988.")
    p = pseudonymise(t)
    for v in ["+49 30 2287 4410", "max@example.org", "DE29 1001 0010 0987 6543 21", "B-KM 4471", "RS-4471-0932", "14.03.1988"]:
        assert v not in p.text
    assert "[TELEFON_1]" in p.text and "[IBAN_1]" in p.text and "[GEBURTSDATUM_1]" in p.text

def test_consistent_placeholders_and_surname_propagation():
    p = pseudonymise("Mein Name ist Jürgen Wolff. Herr Wolff hat eine Kündigung erhalten.\n\nMit freundlichen Grüßen\nJürgen Wolff")
    assert "Wolff" not in p.text and "Jürgen" not in p.text
    assert p.text.count("[PERSON_1]") == 3

def test_reidentify_roundtrip():
    src = "Mein Name ist Ayşe Yılmaz, Mail ayse@example.de."
    p = pseudonymise(src)
    assert p.reidentify(p.text) == src

def test_authorities_are_not_masked():
    p = pseudonymise("Das Jobcenter Berlin Lichtenberg und das Amtsgericht Wedding haben geschrieben.\n\nGruß\nOlga Ivanova")
    assert "Jobcenter" in p.text and "Amtsgericht Wedding" in p.text

def test_egress_guard_blocks_vault_values_and_raw_identifiers():
    p = pseudonymise("Mein Name ist Jürgen Wolff, ich wohne in der Kastanienallee 12, 10435 Berlin.")
    assert egress_check(json.dumps(p.text, ensure_ascii=False), p.vault.values()) == []
    raw = json.dumps("Bitte schreiben Sie an Jürgen Wolff", ensure_ascii=False)
    assert egress_check(raw, p.vault.values())
    assert egress_check("IBAN DE29 1001 0010 0987 6543 21", [])
    assert egress_check("kontakt: a@b.de", [])
