from datetime import date
from rsagent.coverage import coverage_flags, load_policies, route_queue
from rsagent.deadlines import compute_deadline

P = load_policies()

def flags(area="arbeitsrecht", etype="kein_fristereignis", ds=None, ev=None, received=date(2026, 10, 1), pol="POL-A"):
    dl = compute_deadline(etype, ev) if ev else None
    return coverage_flags(area=area, event_type=etype, dispute_start=ds, deadline=dl,
                          deadline_expected_but_missing=etype != "kein_fristereignis" and dl is None,
                          received_at=received, policy=P[pol])

def test_waiting_period_boundary():
    # POL-C starts 01.07.2026, 3-month waiting period: a dispute on 30.09 is inside, on 01.10 outside
    assert "wartezeit" in flags(ds=date(2026, 9, 30), pol="POL-C")
    assert "wartezeit" not in flags(ds=date(2026, 10, 1), pol="POL-C")

def test_traffic_has_no_waiting_period():
    assert flags(area="verkehrsrecht", ds=date(2026, 8, 20), pol="POL-E") == []

def test_prior_dispute():
    assert flags(ds=date(2026, 6, 30), pol="POL-C") == ["vorvertraglich"]

def test_uninsured_area_and_unknown_area():
    assert flags(area="mietrecht", pol="POL-B") == ["nicht_versichert"]
    assert flags(area="sonstiges") == ["nicht_versichert"]

def test_missing_dispute_date_on_young_policy():
    assert flags(ds=None, pol="POL-E") == ["rs_datum_fehlt"]
    assert flags(ds=None, pol="POL-A") == []

def test_deadline_flags():
    assert "frist_kritisch" in flags(etype="kuendigung_arbeitsvertrag", ev=date(2026, 9, 14), ds=date(2026, 9, 14), received=date(2026, 9, 28))
    assert "frist_abgelaufen" in flags(etype="kuendigung_arbeitsvertrag", ev=date(2026, 9, 1), ds=date(2026, 9, 1), received=date(2026, 10, 1))
    assert "frist_unklar" in flags(etype="kuendigung_arbeitsvertrag", ev=None, ds=date(2026, 9, 1))

def test_social_objection_procedure_coverage():
    assert flags(area="sozialrecht", etype="sozialbescheid", ev=date(2026, 9, 1), ds=date(2026, 9, 1), received=date(2026, 9, 2), pol="POL-B") == ["widerspruch_nicht_gedeckt"]
    assert flags(area="sozialrecht", etype="sozialbescheid", ev=date(2026, 9, 1), ds=date(2026, 9, 1), received=date(2026, 9, 2), pol="POL-A") == []

def test_routing_priorities():
    assert route_queue("arbeitsrecht", ["wartezeit", "frist_kritisch"]) == "prioritaet_frist"
    assert route_queue("mietrecht", ["nicht_versichert"]) == "deckungspruefung"
    assert route_queue("sonstiges", []) == "triage_manuell"
    assert route_queue("verkehrsrecht", []) == "anwalt_verkehrsrecht"

def test_gold_flags_reproduced(all_cases):
    for c in all_cases:
        g = c["gold"]
        dl = compute_deadline(g["event_type"], g["event_date"]) if g["event_date"] else None
        got = coverage_flags(area=g["area"], event_type=g["event_type"], dispute_start=g["dispute_start_date"], deadline=dl,
                             deadline_expected_but_missing=False, received_at=c["received_at"], policy=P[c["policy"]])
        assert sorted(got) == sorted(g["flags"]), c["id"]
