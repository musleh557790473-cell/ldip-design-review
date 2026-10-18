"""Real-data checks of the SPC-0006 Rev I build (phase C2 and K25): TI8 on NMAR, QRWN and NRJS 30 %, TI9 the QRWN
typical sections. Real drawings stay out of Git (brief §10.2): each test runs only when its folder is named —
LDIP_REVH_DATA (NMAR), LDIP_REVH_DATA_QRWN, LDIP_REVH_DATA_NRJS, the same folders as test_revh_real.py — holding the
skill 1 / 2 ingestion files and `rules_RDE_c2.json` (get_rules.py output of a test copy of rules_v6 with ROD-032,
033, 034, 039, 040, 042, 043 active; test copy only, never approved). Expected values measured in build-027
(18 Oct 2026), ezdwg 0.16.0; the QRWN plan 00003 is read by the reader chain (LibreDWG in scratch, L-1 open)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SK = REPO / "plugin/ldip-design-review/skills/reviewing-road-design/scripts"
PY = sys.executable
ND, QD, JD = (os.environ.get(k) for k in ("LDIP_REVH_DATA", "LDIP_REVH_DATA_QRWN", "LDIP_REVH_DATA_NRJS"))
ST = "30% Concept Design"


def run(script, *args):
    p = subprocess.run([PY, str(SK / script), *map(str, args)], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def pipeline(o, d, site, sheets, sections, plan, pdf=None, report=None):
    """B2 network and classes, then C2 layout, as the skill procedure (steps 2, 4, 5a, 5d)."""
    rules = d / "rules_RDE_c2.json"
    crit = []
    if report:
        run("read_criteria.py", "--ingestion", report, "--out", o / "criteria.json")
        crit = ["--criteria", o / "criteria.json"]
    run("check_sections.py", "--sections", sections, "--rules", rules, "--out", o / "sc.json")
    run("read_setout.py", "--ingestion", *sheets, "--out", o / "setout.json")
    run("build_network.py", "--setout", o / "setout.json", "--site", site, "--linework", plan, "--out", o / "network.json")
    run("read_class.py", "--network", o / "network.json", "--site", site, "--linework", plan, *(["--pdf", pdf] if pdf else []),
        *crit, "--sections", o / "sc.json", "--out", o / "class.json")
    run("check_layout.py", "--network", o / "network.json", "--class", o / "class.json", "--rules", rules, "--stage", ST,
        "--file", f"plan_geometry={plan}", *crit, "--out", o / "lay.json")
    return load(o / "lay.json")


def tally(d, rid, cls):
    c = [x for x in d["checks"] if x["rule_id"] == rid and x["class"] == cls and x["parameter"] == "corner_radius"]
    return len(c), sum(x["result"] == "fail" for x in c)


@pytest.mark.skipif(not ND, reason="LDIP_REVH_DATA not set (real drawings stay out of Git)")
def test_ti8_nmar(tmp_path):
    d = Path(ND or ".")
    n = "LDIP-RCRC-SAP-03-DN-ROD-DWG"
    lay = pipeline(tmp_path, d, "NMAR", [d / f"{n}-60000-00{i}-00.dwg.json" for i in (1, 2, 3)], d / f"{n}-30000-001-00.dwg.json",
                   d / f"{n}-00003-001-00.dwg.json", d / f"{n}-00003-001-00.pdf.json", d / "LDIP-RCRC-SAP-03-DN-DES-RPT-00001.pdf.json")
    assert lay["counts"]["corners"] == 104
    assert lay["corners"] == {"local_access": {"10": 63, "10.4": 1, "12": 5},
                              "sub_collector": {"10": 26, "12": 5, "12.5": 1, "15": 2, "22.3": 1}}
    assert tally(lay, "ROD-032", "local_access") == (69, 69) and tally(lay, "ROD-032", "sub_collector") == (35, 27)
    dv = [x for x in lay["checks"] if x["parameter"] == "design_vehicle"]
    assert len(dv) == 1 and dv[0]["result"] == "fail" and dv[0]["departures_quoted"] == ["DEP-04"]
    icd = [(x["roundabout"], x["observed"], x["class"], x["result"]) for x in lay["checks"] if x["rule_id"] == "ROD-039"]
    assert icd == [("RA-01", 40.4, "C-L", "fail")]
    rb = lay["roundabouts"][0]
    assert (rb["island_R"], rb["apron_width"], rb["islands"]) == (12.0, None, [])
    assert any(x["rule_id"] == "ROD-043" and x.get("parameter") == "apron_width" for x in lay["not_run"])


@pytest.mark.skipif(not JD, reason="LDIP_REVH_DATA_NRJS not set (real drawings stay out of Git)")
def test_ti8_nrjs(tmp_path):
    d = Path(JD or ".")
    n = "LDIP-RCRC-SAP-01-NRJS-IRO-DWG-00"
    lay = pipeline(tmp_path, d, "NRJS", [d / f"{n}-60001-0A.dwg.json"], d / f"{n}-30001-0A.dwg.json", d / f"{n}-15000-0A.dwg.json",
                   d / f"{n}-15000-0A.pdf.json")
    assert lay["corners"] == {"local_access": {"11": 2, "12": 12}}             # curb face and edge line counted once
    assert tally(lay, "ROD-032", "local_access") == (14, 14) and lay["roundabouts"] == []
    assert any(x.get("parameter") == "design_vehicle" and "C5" in x["reason"] for x in lay["not_run"])   # no report


@pytest.fixture(scope="module")
def qrwn(tmp_path_factory):
    if not QD:
        pytest.skip("LDIP_REVH_DATA_QRWN not set (real drawings stay out of Git)")
    d = Path(QD)
    o = tmp_path_factory.mktemp("revi_q")
    n = "LDIP-RCRC-SAP-01-AQ-ROD-DWG"
    lay = pipeline(o, d, "QRWN", [d / f"{n}-60000-00{i}-0A.dwg.json" for i in (1, 2)], d / f"{n}-30000-001-0A.dwg.json",
                   d / f"{n}-00003-001-0A.dwg.json", d / f"{n}-00003-001-0A.pdf.json",
                   d / "LDIP-RCRC-SAP-01-AQ-DES-RPT-00001-001.pdf.json")
    return lay, load(o / "sc.json"), load(o / "class.json")


def test_ti8_qrwn(qrwn):
    lay, _, _ = qrwn
    assert lay["counts"]["corners"] == 740
    assert tally(lay, "ROD-032", "local_access") == (558, 558) and tally(lay, "ROD-032", "sub_collector") == (158, 116)
    assert any("10 junction(s) with a road without a class" in x["reason"] for x in lay["not_run"])
    icd = {x["roundabout"]: (x["observed"], x["class"], x["result"]) for x in lay["checks"] if x["rule_id"] == "ROD-039"}
    assert icd == {"RA-1": (40.4, "C-L", "fail"), "RA-2": (40.5, "C-C", "pass"), "RA-3": (40.4, "C-C", "pass"),
                   "RA-4": (40.5, "C-L", "fail"), "RA-5": (40.4, "C-C", "pass"), "RA-6": (40.4, "C-L", "fail"),
                   "RA-7": (40.4, "C-C", "pass")}
    isl = sorted((r["name"], i["approach"], i["width"], i["length"]) for r in lay["roundabouts"] for i in r["islands"])
    assert isl == [("RA-1", "R.1.F.2", 4.34, 7.31), ("RA-1", "R.2.F.3", 4.34, 7.31), ("RA-4", "R.6.F.1", 3.85, 4.66)]
    ew = sorted(x["observed"] for x in lay["checks"] if x["rule_id"] == "ROD-040")
    assert ew == [3.75, 3.76, 4.51]
    assert all(r["apron_width"] is None for r in lay["roundabouts"])


def test_ti9_k25_sections(qrwn):
    _, sc, cls = qrwn
    secs = sorted((s["title"], s["class"], s.get("anchor")) for s in sc["sections"])
    assert [s[0][:6] for s in secs] == ["15 MET", "18 MET", "20 MET", "25m NE", "30m NE", "36m NE"]
    assert all(s[2] == "title" for s in secs)
    rows = sorted(e["value"] for s in sc["sections"] for e in s["elements"] if e["element"] == "row")
    assert rows == [15.0, 18.0, 20.0, 25.0, 30.0, 36.0]
    med = [e["value"] for s in sc["sections"] if s["title"].startswith("30m") for e in s["elements"] if e["element"] == "median"]
    assert med == [4.0]                                    # the centre-line value, not a 4.0 m lane
    assert cls["summary"]["classes"] == {"local_access": 219, "sub_collector": 21} and cls["summary"]["class_unknown"] == 0


@pytest.mark.skipif(not ND, reason="LDIP_REVH_DATA not set (real drawings stay out of Git)")
def test_ti9_nmar_sections_unchanged(tmp_path):
    d = Path(ND or ".")
    r = run("check_sections.py", "--sections", d / "LDIP-RCRC-SAP-03-DN-ROD-DWG-30000-001-00.dwg.json",
            "--rules", d / "rules_RDE_c2.json", "--out", tmp_path / "sc.json")
    assert (r["sections"], r["checks"], r["fail"], r["not_run"]) == (4, 34, 8, 2)
    assert not any("anchor" in s for s in load(tmp_path / "sc.json")["sections"])
