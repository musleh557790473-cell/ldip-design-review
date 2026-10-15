"""Tests for skill 10 phase B1 — presence rules (SPC-0006 Rev E §4.13B). Synthetic data only: rule texts,
clauses and drawings below are made up for the tests (real rules live only in the local rulebook)."""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("jsonschema")

REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / "plugin/ldip-design-review/skills/reviewing-road-design"
SK = SKILL / "scripts"
PY = sys.executable
RUN = "NMAR-T0002-20261015T1300"
sys.path.insert(0, str(SK))
import road_common as rc  # noqa: E402


def run(script, *args, cwd=None):
    p = subprocess.run([PY, str(SK / script), *map(str, args)], capture_output=True, text=True, cwd=cwd)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


STAGE_MAP = {"30% Concept Design": {"phase": "PD", "milestone": None}, "60%": {"phase": "DD", "milestone": 60}}


def PR(rid, phase, carriers, enforcement=None, applic=None, extra_inputs=None):
    """Synthetic presence rule as skill 8 returns it."""
    p = {"value": f"Synthetic element of {rid}", "unit": None, "tag": "AGREED", "source_id": "SRC-T", "clause": "§T",
         "blocking": False, "cap": "Probable" if enforcement == "advisory" else "Confirmed"}
    if enforcement:
        p["enforcement"] = enforcement
    req = extra_inputs or [{"item": f"element of {rid}", "carrier": list(carriers), "required_from_phase": phase,
                            "on_missing": "finding", "source_id": "SRC-T", "clause": "§T"}]
    return {"rule_id": rid, "title": f"Test presence rule {rid}", "skill": "reviewing-road-design", "state": "active",
            "cap": "Confirmed", "statement": f"Synthetic requirement {rid}.", "method": "test", "inputs": list(carriers),
            "parameters": {"required_element": p}, "skip_subchecks_for": [],
            "advisory_parameters": ["required_element"] if enforcement == "advisory" else [], "code_lists": {},
            "severity_default": "Minor", "issue_type": "missing_information", "check_type": "presence",
            "default_status": {"script_output": "Missing information", "dwg_vector": "Missing information",
                               "landxml": "Missing information", "pdf_vector_text": "Probable", "pdf_ocr": "Probable"},
            "exclusions": [], "owner_role": "DMR", "applicability": applic or {"street_class": ["all"]},
            "required_inputs": req, "required_from_phase": phase, "decision_ref": None}


def rules_file(tmp):
    db3 = [{"item": "sidewalks — shown or stated (PD)", "carrier": ["typical_section", "plan_geometry", "design_report"],
            "required_from_phase": "PD", "on_missing": "finding", "source_id": "SRC-T", "clause": "§T"},
           {"item": "sidewalks — drawn (DD)", "carrier": ["typical_section", "plan_geometry"],
            "required_from_phase": "DD", "on_missing": "finding", "source_id": "SRC-T", "clause": "§T"}]
    xing = [dict(db3[0], item="crossings — shown or stated (PD)", carrier=["signing_marking_plan", "plan_geometry", "design_report"]),
            dict(db3[1], item="crossings — drawn (DD)", carrier=["signing_marking_plan", "plan_geometry"])]
    rules = [
        PR("ROD-101", "PD", ["typical_section", "plan_geometry", "design_report"], extra_inputs=db3),
        PR("ROD-104", "PD", ["signing_marking_plan", "plan_geometry", "design_report"], extra_inputs=xing),
        PR("ROD-103", "DD", ["plan_geometry", "details"]),
        PR("ROD-110", "DD", ["signing_marking_plan"]),
        PR("ROD-112", "DD", ["plan_geometry"]),
        PR("ROD-113", "DD", ["plan_geometry", "details"]),
        PR("ROD-115", "PD", ["plan_geometry"], "advisory"),
        PR("ROD-116", "PD", ["typical_section"], "advisory", {"street_class": ["local_access", "sub_collector"]}),
    ]
    p = tmp / "rules_RDE.json"
    p.write_text(json.dumps({"agent": "RDE", "rulebook_version": "v99", "rules": rules, "rules_skipped": [],
                             "stage_map": STAGE_MAP}), encoding="utf-8")
    return p


def dwg(tmp, name, texts=(), layers=(), blocks=()):
    """Synthetic DWG ingestion file (skill 1 shape, only the fields phase B1 reads)."""
    c = {"layers": [{"name": n, "entity_count": k} for n, k in layers],
         "blocks": [{"name": n, "handle": f"B{i}", "layer": "0", "insert": [0, 0, 0]} for i, n in enumerate(blocks)],
         "texts": [{"text": t, "layer": lay, "handle": f"T{i}", "insert": [i * 10.0, 0.0, 0.0], "space": "model"}
                   for i, (t, lay) in enumerate(texts)]}
    ing = {"source": {"path": f"submittals/NMAR/T0002/{name}.dwg", "sha256": "a" * 64, "file_type": "dwg"}, "content": c}
    p = tmp / f"{name}.dwg.json"
    p.write_text(json.dumps(ing), encoding="utf-8")
    return p


def pdf(tmp, name, lines):
    """Synthetic PDF ingestion file (skill 2 shape): one span per word."""
    spans = []
    for i, (page, line) in enumerate(lines):
        x = 50.0
        for w in line.split():
            spans.append((page, {"text": w, "bbox": [x, 100.0 + 12 * i, x + 6 * len(w), 108.0 + 12 * i]}))
            x += 6 * len(w) + 4
    pages = [{"page": n, "spans": [s for pg, s in spans if pg == n], "scanned": False} for n in sorted({pg for pg, _ in spans})]
    ing = {"source": {"path": f"submittals/NMAR/T0002/{name}.pdf", "sha256": "b" * 64, "file_type": "pdf"},
           "content": {"pages": pages}}
    p = tmp / f"{name}.pdf.json"
    p.write_text(json.dumps(ing), encoding="utf-8")
    return p


def sections_file(tmp, walks=2):
    els = [{"element": "sidewalk", "value": 4.0, "assigned": "labelled", "handle": f"S{i}"} for i in range(walks)]
    s = {"tool": {"script": "check_sections.py", "version": "t"}, "mode": "sections", "checks": [], "not_run": [],
         "sections": [{"title": "18 METER LOCAL ROAD", "class": "local_access", "elements": els,
                       "source": "submittals/NMAR/T0002/SEC.dwg"}]}
    p = tmp / "section_checks.json"
    p.write_text(json.dumps(s), encoding="utf-8")
    return p


def presence(tmp, files, stage, rules, sections=None, land_uses=None):
    args = []
    for carrier, f in files:
        args += ["--file", f"{carrier}={f}"]
    code, out = run("find_presence.py", *args, "--out", tmp / "presence.json")
    assert code == 0, out
    extra = ["--sections", sections] if sections else []
    if land_uses is not None:
        extra += ["--land-uses", *land_uses]
    code, out = run("check_presence.py", "--presence", tmp / "presence.json", "--rules", rules, "--stage", stage,
                    *extra, "--out", tmp / "presence_checks.json")
    assert code == 0, out
    return load(tmp / "presence_checks.json")


def by_rule(res, rid, mode=None):
    return [c for c in res["checks"] if c["rule_id"] == rid and (mode is None or c["mode_tag"] == mode)]


# ------------------------------------------------------------------ tests
def test_negation_and_terms():
    hits = rc.term_hits("18 METER LOCAL ROAD (WITHOUT PARALLEL PARKING)", rc.PRESENCE_ELEMENTS["parking_lane"]["terms"])
    assert hits and hits[0][3] is True
    hits = rc.term_hits("on-street visitor parking (0.75 per plot)", rc.PRESENCE_ELEMENTS["parking_lane"]["terms"])
    assert hits and hits[0][3] is False
    assert rc.undecodable("뉪췯") and not rc.undecodable("SIDE WALK")


def test_reference_lists_every_term():
    md = (SKILL / "references/presence_terms.md").read_text(encoding="utf-8")
    for key, el in rc.PRESENCE_ELEMENTS.items():
        assert f"`{key}` | {el['rule']}" in md
        for t in el["terms"]:
            assert f"`{t}`".replace("|", "\\|") in md, t


def test_stated_absent_and_inconsistency(tmp_path):
    rules = rules_file(tmp_path)
    sec = dwg(tmp_path, "SEC", texts=[("15 METER LOCAL ROAD (WITHOUT PARALLEL PARKING)", "TITLE"),
                                      ("18 METER LOCAL ROAD (WITHOUT PARALLEL PARKING)", "TITLE"), ("SIDE WALK", "TXT")])
    rep = pdf(tmp_path, "RPT", [(5, "Residential plots use on-street visitor parking on local roads")])
    res = presence(tmp_path, [("typical_section", sec), ("design_report", rep)], "30% Concept Design", rules)
    stated = by_rule(res, "ROD-116", "presence")
    assert len(stated) == 2 and all(c["observed"] == "stated: not provided" for c in stated)
    inc = by_rule(res, "ROD-116", "presence_inconsistency")
    assert inc and all(c["issue_type"] == "inconsistency" and c["link_cap"] == "Probable" for c in inc)


def test_db3_report_counts_at_pd_only(tmp_path):
    rules = rules_file(tmp_path)
    plan = dwg(tmp_path, "PLAN", layers=[("Road edge", 10)], texts=[("RD-01", "LABEL")])
    rep = pdf(tmp_path, "RPT", [(7, "Accessible pedestrian crossings are proposed at all intersections")])
    files = [("plan_geometry", plan), ("design_report", rep)]
    pd = presence(tmp_path, files, "30% Concept Design", rules)
    assert any(p["rule_id"] == "ROD-104" and p["intent_only"] for p in pd["passed"])
    assert not by_rule(pd, "ROD-104")
    dd = presence(tmp_path, files, "60%", rules)
    f = by_rule(dd, "ROD-104")
    assert f[0]["observed"] == "not shown in any searched file" and "signing_marking_plan" in f[0]["missing_carriers"]
    assert any("stated in the report only" in c["observed"] for c in f)


def test_db4_undecodable_on_element_layer_caps_probable(tmp_path):
    rules = rules_file(tmp_path)
    clean = dwg(tmp_path, "P1", layers=[("Road edge", 5)], texts=[("뉪췯", "0")])
    res = presence(tmp_path, [("plan_geometry", clean), ("details", clean)], "60%", rules)
    assert by_rule(res, "ROD-103")[0]["link_cap"] is None            # undecodable only on layer 0
    dirty = dwg(tmp_path, "P2", layers=[("Road edge", 5)], texts=[("뉪췯", "C-TACTILE")])
    res = presence(tmp_path, [("plan_geometry", dirty), ("details", clean)], "60%", rules)
    c = by_rule(res, "ROD-103")[0]
    assert c["link_cap"] == "Probable" and c["undecodable_on_element_layers"] == 1


def test_db5_partial_coverage(tmp_path):
    rules = rules_file(tmp_path)
    rep = pdf(tmp_path, "RPT", [(3, "continuous walkways provided on both sides of the roads for at least 85% of the network")])
    res = presence(tmp_path, [("design_report", rep), ("typical_section", dwg(tmp_path, "SEC", texts=[("SIDE WALK", "T")]))],
                   "30% Concept Design", rules)
    part = by_rule(res, "ROD-101", "presence_partial")
    assert len(part) == 1 and part[0]["link_cap"] == "Probable" and "85 %" in part[0]["observed"]


def test_stage_gate_and_conditions(tmp_path):
    rules = rules_file(tmp_path)
    plan = dwg(tmp_path, "PLAN", layers=[("Road edge", 10)])
    res = presence(tmp_path, [("plan_geometry", plan)], "30% Concept Design", rules)
    reasons = {n["rule_id"]: n["reason"] for n in res["not_run"]}
    assert reasons["ROD-103"] == "not required at stage" and reasons["ROD-110"] == "not required at stage"
    assert "road-end positions" in reasons["ROD-115"]
    dd = presence(tmp_path, [("plan_geometry", plan)], "60%", rules)
    r = {n["rule_id"]: n["reason"] for n in dd["not_run"]}
    assert r["ROD-112"].startswith("not applicable") and "DLUS" in r["ROD-113"]
    dd2 = presence(tmp_path, [("plan_geometry", plan)], "60%", rules, land_uses=["Residential", "Primary School"])
    assert by_rule(dd2, "ROD-113")                                     # applies, absent -> finding
    dd3 = presence(tmp_path, [("plan_geometry", plan)], "60%", rules, land_uses=["Residential"])
    assert "not applicable" in {n["rule_id"]: n["reason"] for n in dd3["not_run"]}["ROD-113"]
    rb = presence(tmp_path, [("plan_geometry", dwg(tmp_path, "P3", texts=[("ROUNDABOUT RA-01", "T")]))], "60%", rules)
    assert by_rule(rb, "ROD-112")                                      # roundabout shown, no splitter -> finding


def test_section_level_rod101(tmp_path):
    rules = rules_file(tmp_path)
    sec = dwg(tmp_path, "SEC", texts=[("SIDE WALK", "T")])
    res = presence(tmp_path, [("typical_section", sec)], "30% Concept Design", rules, sections=sections_file(tmp_path, walks=1))
    assert len(by_rule(res, "ROD-101", "presence_section")) == 1
    res = presence(tmp_path, [("typical_section", sec)], "30% Concept Design", rules, sections=sections_file(tmp_path, walks=2))
    assert not by_rule(res, "ROD-101", "presence_section")


def test_findings_end_to_end_schema(tmp_path):
    rules = rules_file(tmp_path)
    sec = dwg(tmp_path, "SEC", texts=[("18 METER LOCAL ROAD (WITHOUT PARALLEL PARKING)", "TITLE"), ("SIDE WALK", "T")])
    plan = dwg(tmp_path, "PLAN", layers=[("Road edge", 3)])
    presence(tmp_path, [("typical_section", sec), ("plan_geometry", plan), ("details", plan)], "60%", rules)
    code, out = run("build_candidates.py", "--results", tmp_path / "presence_checks.json", "--rules", rules, "--run", RUN,
                    "--site", "NMAR", "--sub", "T0002", "--evidence-dir", tmp_path / "ev", "--review-root", tmp_path,
                    "--out", tmp_path / "cand.json")
    assert code == 0, out
    cands = load(tmp_path / "cand.json")
    by = {}
    for f in cands:
        by.setdefault(f["reference"]["rule_id"], []).append(f)
    assert by["ROD-116"][0]["proposed_status"] == "Probable"           # advisory cap
    absent = by["ROD-103"][0]
    assert absent["proposed_status"] == "Missing information" and absent["location"] == {"document_no": "PACKAGE", "package_level": True}
    assert re.search(r"searched: .*PLAN", absent["evidence"][0]["extract"])
    assert len(by["ROD-104"]) == 1                                     # one finding per rule (M4)
    code, out = run("validate_findings.py", "--findings", tmp_path / "cand.json", "--rules", rules)
    assert code == 0, out
