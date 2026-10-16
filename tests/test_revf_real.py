"""Real-data checks of the SPC-0006 Rev F build (T-F2 ... T-F4, TC1 ... TC9). Real drawings stay out of Git
(brief §10.2): the tests run only when LDIP_REVF_DATA names a local folder holding the skill 1 / 2 ingestion
files of the session-23 test set (file names below), the QRWN design-report ingestion and a rules file for
agent RDE (`rules_RDE_t115.json`: rules_v4 with ROD-115 set advisory, test copy only). Expected values were
measured in build-025 (16 Oct 2026); the setting-out sheets were converted with ezdwg 0.16.0 (see the change
set: ezdwg 0.12.9 drops table cells on these files)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

DATA = os.environ.get("LDIP_REVF_DATA")
pytestmark = pytest.mark.skipif(not DATA, reason="LDIP_REVF_DATA not set (real drawings stay out of Git)")
REPO = Path(__file__).resolve().parents[1]
SK = REPO / "plugin/ldip-design-review/skills/reviewing-road-design/scripts"
PY = sys.executable
D = Path(DATA or ".")
SITES = {
    "NMAR": {"sheets": "LDIP-RCRC-SAP-03-DN-ROD-DWG-60000-00*-00.dwg.json", "plan": "LDIP-RCRC-SAP-03-DN-ROD-DWG-00003-001-00",
             "sections": "LDIP-RCRC-SAP-03-DN-ROD-DWG-30000-001-00.dwg.json"},
    "QRWN": {"sheets": "LDIP-RCRC-SAP-01-AQ-ROD-DWG-60000-00[12]-0A.dwg.json", "plan": "LDIP-RCRC-SAP-01-AQ-ROD-DWG-00003-001-0A",
             "criteria": "LDIP-RCRC-SAP-01-AQ-DES-RPT-00001-001.pdf.json"},
    "NRJS": {"sheets": "LDIP-RCRC-SAP-01-NRJS-IRO-DWG-00-60001-0A.dwg.json", "plan": "LDIP-RCRC-SAP-01-NRJS-IRO-DWG-00-15000-0A"},
}


def run(script, *args):
    p = subprocess.run([PY, str(SK / script), *map(str, args)], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    t = tmp_path_factory.mktemp("revf")
    res = {}
    for site, f in SITES.items():
        o = t / site
        o.mkdir()
        plan = D / f"{f['plan']}.dwg.json"
        run("read_setout.py", "--ingestion", *sorted(D.glob(f["sheets"])), "--out", o / "setout.json")
        run("build_network.py", "--setout", o / "setout.json", "--site", site, "--linework", plan, "--out", o / "network.json")
        run("cross_check.py", "--network", o / "network.json", "--site", site, "--linework", plan, "--out", o / "xc.json")
        extra = []
        if f.get("criteria"):
            run("read_criteria.py", "--ingestion", D / f["criteria"], "--out", o / "criteria.json")
            extra += ["--criteria", o / "criteria.json"]
        if f.get("sections"):
            run("check_sections.py", "--sections", D / f["sections"], "--rules", D / "rules_RDE_t115.json", "--out", o / "sc.json")
            extra += ["--sections", o / "sc.json"]
        pdf = D / f"{f['plan']}.pdf.json"
        if pdf.is_file():
            extra += ["--pdf", pdf]
        run("read_class.py", "--network", o / "network.json", "--site", site, "--linework", plan, *extra, "--out", o / "class.json")
        loc_args = []
        for i, x in enumerate(extra):
            if x in ("--criteria", "--sections"):
                loc_args += [x, extra[i + 1]]
        run("check_locations.py", "--network", o / "network.json", "--class", o / "class.json", "--rules", D / "rules_RDE_t115.json",
            "--stage", "30% Concept Design", "--file", f"plan_geometry={plan}", *loc_args, "--cross-check", o / "xc.json",
            "--out", o / "loc.json")
        res[site] = o
    return res


def test_tc1_tc2_tc3_tc4_nmar(out):
    o = out["NMAR"]
    so = load(o / "setout.json")["counts"]
    assert (so["alignments"], so["segments"], so["tangents"], so["curves"]) == (42, 100, 67, 33)
    n = load(o / "network.json")
    assert n["counts"]["junctions"] == 51 and n["counts"]["ends"] == {"dead_end": 21, "site_exit": 5, "gap": 0}
    assert sorted(a["road"] for a in n["roundabouts"][0]["approaches"]) == ["RD-13", "RD-32", "RD-M-01", "RD-M-02"]
    xc = load(o / "xc.json")
    assert [c["subject"].split(":")[0] for c in xc["checks"]] == ["RD-26"] and "L43" in xc["checks"][0]["observed"]
    c = load(o / "class.json")
    assert c["summary"]["classes"] == {"local_access": 38, "sub_collector": 3} and c["summary"]["disagreements"] == 0
    assert c["pairwise_agreement"]["M1=M5"] == "40 / 40"


def test_tc5_tc6_qrwn(out):
    o = out["QRWN"]
    so = load(o / "setout.json")["counts"]
    assert (so["alignments"], so["segments"]) == (250, 478)
    n = load(o / "network.json")["counts"]
    assert n["junctions"] == 329 and n["roundabouts"] == 7 and n["roundabout_approaches"] == 26 and n["ends"]["dead_end"] == 91
    c = load(o / "class.json")
    assert sorted(k for k, r in c["roads"].items() if r.get("disagreement")) == ["R.1.E.1", "R.13.G.15", "R.14.F.4"]
    assert c["summary"]["class_unknown"] == 9 and c["m4_fit"]["median_residual_m"] < 1.0
    assert sorted(c["subject"].split(":")[0] for c in load(o / "xc.json")["checks"]) == ["R.1.F.1", "R.2.F.2"]


def test_tc7_nrjs(out):
    o = out["NRJS"]
    assert load(o / "setout.json")["counts"]["alignments"] == 7
    assert load(o / "class.json")["pairwise_agreement"] == {"M2=M5": "7 / 7"}
    assert load(o / "network.json")["counts"]["ends"]["site_exit"] == 7


def test_tc8_tc9_dead_ends(out):
    nm = [c for c in load(out["NMAR"] / "loc.json")["checks"] if c["rule_id"] == "ROD-115"]
    qr = [c for c in load(out["QRWN"] / "loc.json")["checks"] if c["rule_id"] == "ROD-115"]
    assert len(nm) == 19 and len(qr) == 82
    assert "DEP-05" in qr[0]["observed"] and "DEP-06" in qr[0]["observed"]


def test_tf2_tf3_tf4_linework():
    """T-F2 (K21-2 acceptance), T-F3, T-F4: line work and expanded texts of the three hierarchy / plan drawings."""
    def layers(stem):
        lw = load(D / f"{stem}.linework.json")
        return {v["layer_short"]: (v["records"], round(v["length"])) for v in lw["layers"].values()
                if v["layer_short"] in ("15 m ROW", "18 m ROW", "20m ROW", "25m ROW", "30m ROW", "36m ROW", "60m ROW",
                                        "RD_PR_CNRL", "RD_ROW", "_CL", "RD_CL", "RD_CL External Road")
                and not v["layer_short"].startswith("__")} if lw else {}
    q = layers("LDIP-RCRC-SAP-01-AQ-ROD-DWG-00003-001-0A")
    assert {k: v[0] for k, v in q.items()} == {"15 m ROW": 185, "18 m ROW": 22, "20m ROW": 10, "25m ROW": 10, "30m ROW": 4,
                                               "36m ROW": 1, "60m ROW": 1, "RD_PR_CNRL": 332, "RD_ROW": 260}
    assert q["RD_PR_CNRL"][1] == 64349
    ing = load(D / "LDIP-RCRC-SAP-01-AQ-ROD-DWG-00003-001-0A.dwg.json")
    assert sum(1 for t in ing["content"]["texts"] if t.get("layer_short") == "0-Alignment Name" and t.get("via_block")) == 468
    n = layers("LDIP-RCRC-SAP-03-DN-ROD-DWG-00003-001-00")
    assert n["_CL"] == (47, 11813) and (n["15 m ROW"][0], n["18 m ROW"][0], n["25m ROW"][0]) == (34, 5, 4)
    j = layers("LDIP-RCRC-SAP-01-NRJS-IRO-DWG-00-15000-0A")
    assert j["RD_CL"][0] == 7 and j["RD_CL External Road"][0] == 4
