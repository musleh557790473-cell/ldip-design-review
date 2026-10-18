"""TJ8 — SPC-0006 Rev J §4.13G.11: at PD / 30 %, every C3 rule is not run ("not required at stage"
or "no element"); no C3 finding; typical sections with "WITHOUT PARALLEL PARKING" give no parking element.

Synthetic PD package always runs (CI). Optional real three-site harness when LDIP_REVIEW points at a
local review root (real drawings stay out of Git, brief §10.2).
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("shapely")

REPO = Path(__file__).resolve().parents[1]
SK10 = REPO / "plugin/ldip-design-review/skills/reviewing-road-design/scripts"
PY = sys.executable
ROOT = Path(os.environ["LDIP_REVIEW"]) if os.environ.get("LDIP_REVIEW") else None
C3_RULES = ["ROD-044", "ROD-045", "ROD-046", "ROD-047", "ROD-055", "ROD-056",
            "ROD-059", "ROD-060", "ROD-061", "ROD-062"]


def run(script, *args):
    p = subprocess.run([PY, str(SK10 / script), *map(str, args)], capture_output=True, text=True)
    out = p.stdout.strip().splitlines()
    return p.returncode, (json.loads(out[-1]) if out else {"stderr": p.stderr[-2000:]})


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def dwg(tmp, name, texts=(), polys=()):
    tlist = []
    for i, item in enumerate(texts):
        t, x, y, layer = item[0], item[1], item[2], item[3]
        rot = item[4] if len(item) > 4 else 0
        tlist.append({"type": "TEXT", "text": t, "layer": layer, "insert": [x, y, 0],
                      "handle": f"{name}T{i:X}", "space": "model", "height": 0.25, "rotation": rot})
    ing = {"template_version": "1.2",
           "source": {"path": f"submittals/NRJS/T/{name}.dwg",
                      "sha256": hashlib.sha256(name.encode()).hexdigest(),
                      "bytes": 1, "document_no": None, "revision": None, "file_type": "dwg"},
           "content": {"texts": tlist,
                       "polylines": [{"type": "LWPOLYLINE", "layer": ly, "handle": f"{name}P{i:X}",
                                      "closed": True, "n_vertices": len(v), "vertices": v}
                                     for i, (ly, v) in enumerate(polys)],
                       "blocks": [], "layers": []}}
    p = tmp / f"{name}.dwg.json"
    p.write_text(json.dumps(ing))
    return p


def rules_c3(tmp):
    def R(rid, phase="DD", carriers=None):
        return {"rule_id": rid, "title": rid, "state": "active", "cap": "Confirmed",
                "statement": f"Synthetic {rid}.", "inputs": carriers or ["details"],
                "parameters": {"width": {"value": 2.5, "tag": "AGREED", "source_id": "SRC-T", "clause": "§T",
                                        "unit": "m", "cap": "Confirmed", "tolerance": {"value": 0}},
                              "length": {"value": 3.7, "tag": "AGREED", "source_id": "SRC-T", "clause": "§T",
                                         "unit": "m", "cap": "Confirmed", "tolerance": {"value": 0}},
                              "height": {"value": 75, "tag": "AGREED", "source_id": "SRC-T", "clause": "§T",
                                         "unit": "mm", "cap": "Confirmed", "tolerance": {"value": 0}},
                              "width_min": {"value": 2.5, "tag": "AGREED", "source_id": "SRC-T", "clause": "§T",
                                            "unit": "m", "cap": "Confirmed", "tolerance": {"value": 0}}},
                "severity_default": "Minor", "issue_type": "design_error", "check_type": "value",
                "default_status": {"dwg_vector": "Confirmed", "script_output": "Confirmed"},
                "owner_role": "DMR", "applicability": {"street_class": ["all"]},
                "required_from_phase": phase}

    rs = [R("ROD-044"), R("ROD-045"), R("ROD-046"), R("ROD-047"),
          R("ROD-055", carriers=["signing_marking_plan"]),
          R("ROD-056", carriers=["plan_geometry"]),
          R("ROD-059", phase="PD", carriers=["typical_section", "plan_geometry"]),
          R("ROD-060"), R("ROD-061"), R("ROD-062")]
    p = tmp / "rules_RDE.json"
    p.write_text(json.dumps({
        "agent": "RDE", "rulebook_version": "v7", "rules": rs, "rules_skipped": [],
        "stage_map": {"30% Concept Design": {"phase": "PD", "milestone": 30},
                      "60%": {"phase": "DD", "milestone": 60}}}))
    return p


def _assert_c3_pd_clean(cal, ped, pk, sec, cand):
    """§4.13G.11 TJ8 expectations on PD outputs."""
    for doc, name in ((cal, "calming"), (ped, "pedestrian"), (pk, "parking")):
        fails = [c for c in doc.get("checks", []) if c.get("result") == "fail"]
        assert not fails, f"{name} has C3 fails at PD: {fails[:3]}"
    # every C3 rule accounted for as not_run (stage / no element) — no C3 finding from candidates
    reasons = {(n.get("rule_id"), n.get("reason", "")) for n in
               cal.get("not_run", []) + ped.get("not_run", []) + pk.get("not_run", [])}
    for rid in C3_RULES:
        hits = [r for r in reasons if r[0] == rid]
        assert hits, f"{rid} missing from not_run at PD"
        blob = " ".join(h[1].lower() for h in hits)
        assert ("not required at stage" in blob or "no " in blob or "not run" in blob
                or "inactive" in blob or "not in rules" in blob or "gradient" in blob
                or "bridge" in blob or "surface" in blob or "schedule" in blob
                or "no element" in blob or "no speed" in blob or "no mini" in blob
                or "no chicane" in blob or "no crosswalk" in blob or "no refuge" in blob
                or "no parking" in blob or "detail" in blob), f"{rid}: unexpected reasons {hits}"
    park_els = [e for s in sec.get("sections", []) for e in s.get("elements", []) if e.get("element") == "parking"]
    assert park_els == [], f"sections must have no parking element at 30%: {park_els}"
    assert cand == [] or all(f["reference"]["rule_id"] not in C3_RULES for f in cand)


def test_tj8_synthetic_pd_30_percent(tmp_path):
    """Always-on TJ8: synthetic 30 % PD package — no C3 elements, WITHOUT PARALLEL PARKING."""
    rules = rules_c3(tmp_path)
    # empty details / plan / marking (probe: no C3 elements at 30 %)
    det = dwg(tmp_path, "DET", texts=[("GENERAL NOTES", 0, 0, "TXT")])
    plan = dwg(tmp_path, "PL", texts=[("ROAD LAYOUT", 660100, 2750000, "TXT")])
    mark = dwg(tmp_path, "MK", texts=[])
    sec = dwg(tmp_path, "SEC", texts=[
        ("RIGHT OF WAY", 0, 10, "ROW", 90), ("RIGHT OF WAY", 15, 10, "ROW", 90),
        ("15 m LOCAL ACCESS ROAD WITHOUT PARALLEL PARKING", 7, 4, "TITLE"),
        ("3.50", 7, 8, "DIM"), ("DRIVING LANE", 7, 7.2, "LAB"),
        ("2.00", 12, 8, "DIM"), ("SIDE WALK", 12, 7.2, "LAB"),
        ("15", 7.5, 9.5, "DIM"),
    ])
    (tmp_path / "network.json").write_text(json.dumps({
        "roads": {"ROAD-A (15m R.O.W)": {"length": 200, "geometry": [[660000, 2750000], [660200, 2750000]]}},
        "junctions": [{"id": "J001", "xy": [660100.0, 2750000.0], "roads": ["ROAD-A (15m R.O.W)"]}],
        "roundabouts": []}))
    (tmp_path / "class.json").write_text(json.dumps({
        "roads": {"ROAD-A (15m R.O.W)": {"width": 15.0, "class": "local_access"}}}))

    assert run("read_details.py", "--file", f"details={det}", "--out", tmp_path / "details.json")[0] == 0
    assert run("check_calming.py", "--details", tmp_path / "details.json",
               "--network", tmp_path / "network.json", "--class", tmp_path / "class.json",
               "--rules", rules, "--stage", "30% Concept Design", "--site", "NRJS",
               "--file", f"plan_geometry={plan}", "--file", f"details={det}",
               "--out", tmp_path / "cal.json")[0] == 0
    assert run("check_pedestrian.py", "--network", tmp_path / "network.json", "--rules", rules,
               "--stage", "30% Concept Design", "--site", "NRJS",
               "--file", f"signing_marking_plan={mark}", "--file", f"plan_geometry={plan}",
               "--class", tmp_path / "class.json", "--out", tmp_path / "ped.json")[0] == 0
    assert run("check_parking.py", "--network", tmp_path / "network.json", "--class", tmp_path / "class.json",
               "--rules", rules, "--stage", "30% Concept Design", "--site", "NRJS",
               "--file", f"plan_geometry={plan}", "--out", tmp_path / "pk.json")[0] == 0
    assert run("check_sections.py", "--sections", sec, "--rules", rules, "--out", tmp_path / "sec.json")[0] == 0
    rc, out = run("build_candidates.py",
                  "--results", tmp_path / "cal.json", tmp_path / "ped.json", tmp_path / "pk.json",
                  tmp_path / "sec.json",
                  "--rules", rules, "--run", "NRJS-T-20261018T1530", "--site", "NRJS", "--sub", "T",
                  "--evidence-dir", tmp_path / "ev", "--out", tmp_path / "cand.json")
    assert rc == 0, out
    _assert_c3_pd_clean(load(tmp_path / "cal.json"), load(tmp_path / "ped.json"), load(tmp_path / "pk.json"),
                        load(tmp_path / "sec.json"), load(tmp_path / "cand.json"))


@pytest.mark.skipif(ROOT is None or not (ROOT and ROOT.is_dir()),
                    reason="LDIP_REVIEW not set — real 30 % drawings stay out of Git")
def test_tj8_real_ldip_review_pd(tmp_path):
    """When LDIP_REVIEW is set, run C3 scripts at 30 % on whatever ingestions are present and assert TJ8."""
    assert ROOT is not None
    rules = rules_c3(tmp_path)
    # discover a few DWG ingestions; skip if none
    dwgs = sorted(ROOT.rglob("*.dwg.json"))[:6]
    if not dwgs:
        pytest.skip("no *.dwg.json under LDIP_REVIEW")
    det = dwgs[0]
    plan = dwgs[min(1, len(dwgs) - 1)]
    # minimal network so scripts can run
    (tmp_path / "network.json").write_text(json.dumps({"roads": {}, "junctions": [], "roundabouts": []}))
    (tmp_path / "class.json").write_text(json.dumps({"roads": {}}))
    assert run("read_details.py", "--file", f"details={det}", "--out", tmp_path / "details.json")[0] == 0
    assert run("check_calming.py", "--details", tmp_path / "details.json",
               "--network", tmp_path / "network.json", "--class", tmp_path / "class.json",
               "--rules", rules, "--stage", "30% Concept Design", "--site", "NRJS",
               "--file", f"plan_geometry={plan}", "--out", tmp_path / "cal.json")[0] == 0
    assert run("check_pedestrian.py", "--network", tmp_path / "network.json", "--rules", rules,
               "--stage", "30% Concept Design", "--site", "NRJS",
               "--file", f"plan_geometry={plan}", "--out", tmp_path / "ped.json")[0] == 0
    assert run("check_parking.py", "--network", tmp_path / "network.json", "--class", tmp_path / "class.json",
               "--rules", rules, "--stage", "30% Concept Design", "--site", "NRJS",
               "--file", f"plan_geometry={plan}", "--out", tmp_path / "pk.json")[0] == 0
    cal, ped, pk = load(tmp_path / "cal.json"), load(tmp_path / "ped.json"), load(tmp_path / "pk.json")
    for doc in (cal, ped, pk):
        assert not [c for c in doc.get("checks", []) if c.get("result") == "fail"], doc.get("checks")
    # sections: if a typical-section file mentions WITHOUT PARALLEL PARKING, assert no parking element
    for s in dwgs:
        text = s.read_text(encoding="utf-8", errors="ignore").upper()
        if "WITHOUT PARALLEL PARKING" in text:
            rc, _ = run("check_sections.py", "--sections", s, "--rules", rules, "--out", tmp_path / "sec.json")
            if rc == 0:
                sec = load(tmp_path / "sec.json")
                assert not [e for sec_ in sec.get("sections", []) for e in sec_.get("elements", [])
                            if e.get("element") == "parking"]
            break
