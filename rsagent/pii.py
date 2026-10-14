"""Pseudonymisation and egress guard.

Legal-protection cases are covered by professional secrecy (§ 203 StGB) and the GDPR. Before any
text leaves for an external LLM, personal data is replaced by stable placeholders ([PERSON_1],
[IBAN_1], ...). The mapping stays in a per-case vault on our side and is only used to re-identify
the final letter *after* a lawyer has approved it.

Detection is layered: high-precision regexes for structured identifiers, contextual patterns
("Herr …", "Versicherungsnummer …", signatures), spaCy's German NER for names and organisations,
and propagation so that a surname found once is masked everywhere it appears.

The egress guard is the last line: every outbound payload is scanned for vault values and generic
identifier patterns, and the call is blocked (fail closed) if anything is found.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

# ---------------------------------------------------------------- patterns

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){3,7}(?:[ ]?[A-Z0-9]{1,3})?\b")
PHONE = re.compile(r"(?<![\w/.-])(?:\+49|0049|0)[ ]?(?:\(0\)[ ]?)?\d{2,5}(?:[ /-]?\d{2,5}){1,4}(?![\w/.-])")
# German licence plate: district code, letters, digits ("B-KM 4471", "B AV 2207", "M-AB 12E")
PLATE = re.compile(r"\b[A-ZÄÖÜ]{1,3}[- ][A-Z]{1,2}[ ]?\d{1,4}[EH]?\b")
STREET_SUFFIX = (
    r"(?:stra(?:ß|ss)e|str\.|weg|allee|platz|gasse|damm|ring|ufer|chaussee|steig|promenade|"
    r"park|markt|zeile|hof|berg|grund|graben|tor|brücke|kamp|pfad)"
)
_CAP = r"[A-ZÄÖÜ][\wäöüß.]*"
ADDRESS = re.compile(
    rf"(?:\b(?:Am|An der|Im|In der|Auf dem|Zum|Zur)\s+(?:{_CAP}[ -])*{_CAP}"           # "Am Treptower Park 54"
    rf"|\b(?:{_CAP}[ -])*(?:{_CAP})?(?i:{STREET_SUFFIX}))"                              # "Frankfurter Allee 102"
    r"\s+\d{1,4}(?:\s?[a-zA-Z](?![\wäöüß]))?(?!\s?(?:km|m\b|%|Euro|€|Uhr|Prozent))(?!\d)"
)
PLZ = re.compile(r"\b\d{5}(?=\s+[A-ZÄÖÜ][a-zäöüß])")
ID_KEYWORDS = (
    r"(?:Versicherungsschein(?:nummer|-?nr\.?)|Versicherungs(?:nummer|-?nr\.?)|Vers\.-?Nr\.?|Vertrags(?:nummer|-?nr\.?)|"
    r"Versicherten(?:nummer|-?nr\.?)|Kunden(?:nummer|-?nr\.?)|Personal(?:nummer|-?nr\.?)|Mitglieds(?:nummer|-?nr\.?)|"
    r"Aktenzeichen|Az\.|Kassenzeichen|Geschäftszeichen|Bestell(?:nummer|-?nr\.?)|Rechnungs(?:nummer|-?nr\.?)|"
    r"BG-?Nummer|Bedarfsgemeinschaftsnummer|Steuer-?ID|Schaden(?:nummer|-?nr\.?)|Fall(?:nummer|-?nr\.?))"
)
ID_AFTER_KEYWORD = re.compile(
    rf"{ID_KEYWORDS}\s*(?:ist|lautet)?\s*[:#]?\s*(?:ist\s+)?(?P<id>[A-Z0-9][A-Z0-9 ./-]*?[A-Z0-9])(?=\s*(?:[,;)]|\.\s|\.$|$|\n|\s+(?:und|Sie|bitte)\b))",
    re.IGNORECASE | re.MULTILINE,
)
DOB = re.compile(
    r"(?:geb\.|geboren(?:\s+am)?|Geburtsdatum:?)\s*(?P<dob>\d{1,2}\.\d{1,2}\.\d{2,4})", re.IGNORECASE
)
COMPANY = re.compile(
    r"(?:[A-ZÄÖÜ][\wäöüß&.+-]*(?:\s+(?:&\s+)?[A-ZÄÖÜ][\wäöüß&.+-]*){0,4})\s+"
    r"(?:GmbH\s*&\s*Co\.\s*KG|gGmbH|GmbH|mbH|AG|KG|UG|GbR|OHG|e\.\s?V\.|& Co\. KG|& Partner|& Söhne(?:\s+GmbH)?)"
)
# "Herr Schuster", "Frau Dr. Meier", "Herrn Klaus Wendt", "Familie Kowalski", "Bruder Martin Graf"
TITLED_NAME = re.compile(
    r"\b(?:Herrn?|Frau|Familie|Bruder|Schwester|Cousine|Cousin|Tante|Onkel|Vermieterin?|Chefin?)\s+"
    r"(?:Dr\.\s+|Prof\.\s+)?(?P<name>[A-ZÄÖÜ][a-zäöüßıçşğ]+(?:[- ][A-ZÄÖÜ][a-zäöüßıçşğ]+)?)"
)
SELF_INTRO = re.compile(
    r"(?:mein Name ist|ich heiße|ich,|ich bin)\s+(?P<name>[A-ZÄÖÜ][\wäöüßıçşğ]+\s+[A-ZÄÖÜ][\wäöüßıçşğ-]+)"
)
SIGN_OFF = re.compile(
    r"(?im)^(?:mit freundlichen grüßen|freundliche grüße|viele grüße|beste grüße|liebe grüße|gruß|grüße|"
    r"danke und gruß|vielen dank)[,!.]?\s*$"
)
# Businesses named by trade + proper name, without a legal-form suffix ("Pflegedienst Sonnenschein").
# Added after the blind test run showed this as the only leak class (see results/README).
TRADE_NAME = re.compile(
    r"\b(?:Pflegedienst|Hotel|Café|Cafe|Bäckerei|Kita|Praxis|Autohaus|Restaurant|Kanzlei|Apotheke|Fahrschule|"
    r"Druckerei|Spedition|Reinigungsfirma|Hausverwaltung|Möbelhaus|Fitnessstudio|Elektro|Haustechnik|Pizzeria|"
    r"Friseur|Salon|Werkstatt|Baufirma|Dachdecker|Gärtnerei|Metzgerei|Klinik|Pflegeheim|Supermarkt|Imbiss)"
    r"\s+[A-ZÄÖÜ][\wäöüß-]+(?:\s+[A-ZÄÖÜ][\wäöüß-]+)?"
)
INLINE_SIGN_OFF = re.compile(r"(?i)\bGruß,\s+(?P<name>[A-ZÄÖÜ][\wäöüßıçşğ]+\s+[A-ZÄÖÜ][\wäöüßıçşğ-]+)")

# Public bodies, courts and generic words that NER tags as ORG/PER but that the lawyer and the LLM
# need for routing. They identify nobody.
PUBLIC_BODY = re.compile(
    r"(?i)(amtsgericht|landgericht|arbeitsgericht|sozialgericht|gericht|jobcenter|agentur für arbeit|"
    r"rentenversicherung|krankenkasse|pflegekasse|bußgeldstelle|polizei|behörde|finanzamt|bezirksamt|"
    r"bundes|landes|stadt berlin|senat|mietspiegel|kappungsgrenze|betriebsrat|ki[- ]system|system|"
    r"versicherung$|navi|scanner|umweltzone|plakette|probezeit|reha|bürgergeld|krankengeld|pflegegrad|"
    r"arbeitslosengeld|sperrzeit|abmahnung|kündigung|mahnbescheid|bußgeldbescheid|testament|pflichtteil)"
)
NAME_STOPWORDS = {
    "Herr", "Herrn", "Frau", "Familie", "Dr", "Prof", "Sehr", "Hallo", "Guten", "Tag", "Danke", "Gruß",
    "Grüße", "Mit", "Ich", "Mein", "Meine", "Die", "Der", "Das", "Bitte", "Telefon", "Tel", "Hinweis",
    "Berlin", "Ostern", "Sonnenallee", "System", "Assistent", "Anweisung", "Note", "Ignore", "Bruder",
    "Schwester", "Vermieter", "Vermieterin", "Chef", "Chefin", "Gutachter", "Postbote", "Arzt", "Kreta",
}


@dataclass
class Span:
    start: int
    end: int
    label: str
    text: str


@dataclass
class Vault:
    """Placeholder ↔ original mapping for one case. Never leaves our side."""
    to_placeholder: dict[str, str] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)

    def placeholder(self, label: str, value: str) -> str:
        key = value.strip().lower()
        if key not in self.to_placeholder:
            self.counters[label] = self.counters.get(label, 0) + 1
            self.to_placeholder[key] = f"[{label}_{self.counters[label]}]"
        return self.to_placeholder[key]

    def alias(self, value: str, placeholder: str) -> None:
        self.to_placeholder.setdefault(value.strip().lower(), placeholder)

    @property
    def originals(self) -> dict[str, str]:
        """placeholder -> canonical original (first value registered for it)."""
        out: dict[str, str] = {}
        for value, ph in self.to_placeholder.items():
            out.setdefault(ph, value)
        return out

    def values(self) -> list[str]:
        return list(self.to_placeholder)


@lru_cache(maxsize=1)
def _nlp():
    import spacy

    return spacy.load("de_core_news_md", disable=["lemmatizer", "morphologizer", "attribute_ruler"])


@dataclass
class Pseudonymised:
    text: str
    vault: Vault
    spans: list[Span]
    _original_case: dict[str, str] = field(default_factory=dict)

    def reidentify(self, text: str) -> str:
        for ph, original_lower in self.vault.originals.items():
            text = text.replace(ph, self._original_case.get(ph, original_lower))
        return text


def _signature_names(text: str) -> list[Span]:
    spans: list[Span] = []
    lines = text.splitlines(keepends=True)
    offsets, pos = [], 0
    for ln in lines:
        offsets.append(pos)
        pos += len(ln)
    for i, ln in enumerate(lines):
        if SIGN_OFF.match(ln.strip()):
            for j in range(i + 1, min(i + 3, len(lines))):
                cand = lines[j].strip()
                m = re.match(r"([A-ZÄÖÜ][\wäöüßıçşğ]+(?:[- ][A-ZÄÖÜ][\wäöüßıçşğ-]+)+)", cand)
                if m:
                    start = offsets[j] + lines[j].index(m.group(1))
                    spans.append(Span(start, start + len(m.group(1)), "PERSON", m.group(1)))
                    break
    # A bare name on its own line near the end (common in short emails without a sign-off)
    tail = [k for k in range(len(lines)) if lines[k].strip()][-3:]
    for k in tail:
        cand = lines[k].strip()
        m = re.fullmatch(r"([A-ZÄÖÜ][\wäöüßıçşğ]+(?:[- ][A-ZÄÖÜ][\wäöüßıçşğ-]+){1,2})(?:,.*)?", cand)
        if (m and not SIGN_OFF.match(cand) and not PUBLIC_BODY.search(m.group(1))
                and m.group(1).split()[0] not in NAME_STOPWORDS):
            start = offsets[k] + lines[k].index(m.group(1))
            spans.append(Span(start, start + len(m.group(1)), "PERSON", m.group(1)))
    return spans


def detect(text: str, use_ner: bool = True) -> list[Span]:
    spans: list[Span] = []

    def add(m: re.Match, label: str, group: int | str = 0):
        s, e = m.span(group)
        spans.append(Span(s, e, label, text[s:e]))

    for m in EMAIL.finditer(text):
        add(m, "EMAIL")
    for m in IBAN.finditer(text):
        if sum(c.isdigit() for c in m.group()) >= 12:
            add(m, "IBAN")
    for m in ID_AFTER_KEYWORD.finditer(text):
        add(m, "NUMMER", "id")
    for m in DOB.finditer(text):
        add(m, "GEBURTSDATUM", "dob")
    for m in PHONE.finditer(text):
        if sum(c.isdigit() for c in m.group()) >= 8:
            add(m, "TELEFON")
    for m in PLATE.finditer(text):
        add(m, "KENNZEICHEN")
    for m in ADDRESS.finditer(text):
        add(m, "ADRESSE")
    for m in PLZ.finditer(text):
        add(m, "PLZ")
    for m in COMPANY.finditer(text):
        val = m.group().strip()
        # drop leading articles/prepositions captured by the greedy capital-letter run
        val2 = re.sub(r"^(?:Die|Der|Das|Den|Dem|Bei|Mein|Meine|Meinem|Meiner|Vermieter|Vermieterin|Arbeitgeber)\s+", "", val)
        if not PUBLIC_BODY.search(val2):
            s = m.start() + (len(val) - len(val2))
            spans.append(Span(s, m.end(), "FIRMA", text[s:m.end()]))
    for m in TRADE_NAME.finditer(text):
        if not PUBLIC_BODY.search(m.group()):
            add(m, "FIRMA")
    for m in TITLED_NAME.finditer(text):
        if m.group("name") not in NAME_STOPWORDS:
            add(m, "PERSON", "name")
    for m in SELF_INTRO.finditer(text):
        add(m, "PERSON", "name")
    for m in INLINE_SIGN_OFF.finditer(text):
        add(m, "PERSON", "name")
    spans += _signature_names(text)

    if use_ner:
        doc = _nlp()(text)
        for ent in doc.ents:
            if ent.label_ == "PER":
                toks = [t for t in re.findall(r"[\wäöüßıçşğ-]+", ent.text) if t[:1].isupper()]
                if toks and not all(t in NAME_STOPWORDS for t in toks) and not PUBLIC_BODY.search(ent.text):
                    spans.append(Span(ent.start_char, ent.end_char, "PERSON", ent.text))
            elif ent.label_ == "ORG" and not PUBLIC_BODY.search(ent.text) and len(ent.text) > 2:
                spans.append(Span(ent.start_char, ent.end_char, "FIRMA", ent.text))
    return spans


_PRIORITY = {"EMAIL": 0, "IBAN": 1, "NUMMER": 2, "GEBURTSDATUM": 3, "TELEFON": 4, "KENNZEICHEN": 5,
             "ADRESSE": 6, "FIRMA": 7, "PERSON": 8, "PLZ": 9}


def _resolve(spans: list[Span]) -> list[Span]:
    """Keep non-overlapping spans; prefer high-precision labels, then longer spans."""
    spans = sorted(spans, key=lambda s: (_PRIORITY[s.label], -(s.end - s.start), s.start))
    chosen: list[Span] = []
    for s in spans:
        if all(s.end <= c.start or s.start >= c.end for c in chosen):
            chosen.append(s)
    return sorted(chosen, key=lambda s: s.start)


def pseudonymise(text: str, use_ner: bool = True) -> Pseudonymised:
    vault = Vault()
    spans = _resolve(detect(text, use_ner))
    original_case: dict[str, str] = {}
    out, last = [], 0
    person_tokens: dict[str, str] = {}
    # Register multi-token names first so "Herr Wolff" reuses the placeholder of "Jürgen Wolff".
    for s in sorted((s for s in spans if s.label == "PERSON"), key=lambda s: -len(s.text.split())):
        toks = [t for t in re.findall(r"[\wäöüßıçşğ-]+", s.text) if len(t) >= 3 and t not in NAME_STOPWORDS]
        known = next((ph for t in toks if (ph := vault.to_placeholder.get(t.lower()))), None)
        if known and len(toks) == 1:
            vault.alias(s.text, known)
            continue
        ph = vault.placeholder("PERSON", s.text)
        if len(toks) > 1:
            for t in toks:
                vault.alias(t, ph)
    for s in spans:
        ph = vault.placeholder(s.label, s.text)
        original_case.setdefault(ph, s.text)
        out.append(text[last:s.start])
        out.append(ph)
        last = s.end
        if s.label == "PERSON":
            for tok in re.findall(r"[\wäöüßıçşğ-]+", s.text):
                if len(tok) >= 3 and tok[:1].isupper() and tok not in NAME_STOPWORDS:
                    person_tokens.setdefault(tok, ph)
        if s.label == "FIRMA":
            core = re.sub(r"\s+(?:GmbH.*|gGmbH|mbH|AG|KG|UG|GbR|OHG|e\.\s?V\.|& Co\. KG|& Partner|& Söhne.*)$", "", s.text)
            if core != s.text and len(core) >= 4:
                person_tokens.setdefault(core, ph)
    out.append(text[last:])
    masked = "".join(out)
    # Propagate: a surname or company core found once is masked wherever else it appears.
    for tok, ph in sorted(person_tokens.items(), key=lambda kv: -len(kv[0])):
        pattern = re.compile(rf"(?<![\w\[]){re.escape(tok)}(?![\w\]])")
        if pattern.search(masked):
            vault.alias(tok, ph)
            masked = pattern.sub(ph, masked)
    return Pseudonymised(masked, vault, spans, original_case)


# ---------------------------------------------------------------- egress guard

GENERIC_EGRESS = [("EMAIL", EMAIL), ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?\d{4}){4,7}\b"))]


class EgressBlocked(RuntimeError):
    pass


def egress_check(payload: str, vault_values: list[str]) -> list[str]:
    """Return the reasons this payload must not leave. Empty list = safe to send."""
    hits: list[str] = []
    low = payload.lower()
    for v in vault_values:
        if len(v) >= 3 and re.search(rf"(?<![\w]){re.escape(v)}(?![\w])", low):
            hits.append(f"vault value present ({len(v)} chars)")
    for name, rx in GENERIC_EGRESS:
        if rx.search(payload):
            hits.append(f"generic {name} pattern present")
    return hits
